"""Explicit OCI and Oracle bindings. Optional SDKs are imported only by build_live.

Each OCI operation disables SDK retries. Workflow/database state owns recovery.
No client is constructed or credential loaded at module import time.
"""
from __future__ import annotations
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket

from .config import unique_pairs, validate_config, valid_messages_endpoint
from .contracts import CloudError


def _error(code, exc):
    return CloudError(code, retryable=getattr(exc, "status", None) in (408, 429, 500, 502, 503, 504))


class OciObjects:
    def __init__(self, sdk, client, config):
        self.sdk, self.client, self.config = sdk, client, config
        self.namespace, self.bucket = config["object_storage_namespace"], config["object_bucket"]

    def _key(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9/_\-.]{1,512}", key) or ".." in key or key.startswith("/"):
            raise CloudError("OBJECT_KEY_INVALID")

    def put_immutable(self, key, content, sha256, mime_type):
        self._key(key)
        if not isinstance(content, bytes) or not content or hashlib.sha256(content).hexdigest() != sha256:
            raise CloudError("OBJECT_CONTENT_HASH_MISMATCH")
        try:
            result = self.client.put_object(self.namespace, self.bucket, key, content,
                content_length=len(content), content_type=mime_type,
                opc_meta={"sha256": sha256}, if_none_match="*",
                retry_strategy=self.sdk.retry.NoneRetryStrategy())
        except Exception as exc:
            if getattr(exc, "status", None) != 412:
                raise _error("OBJECT_UPLOAD_FAILED", exc) from None
            try:
                result = self.client.head_object(self.namespace, self.bucket, key,
                    retry_strategy=self.sdk.retry.NoneRetryStrategy())
                if result.headers.get("opc-meta-sha256") != sha256 or int(result.headers.get("content-length", -1)) != len(content):
                    raise CloudError("IMMUTABLE_OBJECT_CONFLICT")
            except CloudError:
                raise
            except Exception as read_exc:
                raise _error("OBJECT_RECONCILE_FAILED", read_exc) from None
        version = result.headers.get("version-id")
        if not isinstance(version, str) or not version:
            raise CloudError("OBJECT_VERSION_REQUIRED")
        return {"version_id": version}

    def read_bounded(self, key, version_id, maximum):
        self._key(key)
        if not isinstance(version_id, str) or not version_id or type(maximum) is not int or maximum < 1:
            raise CloudError("OBJECT_READ_SCOPE_INVALID")
        response = None
        try:
            response = self.client.get_object(self.namespace, self.bucket, key, version_id=version_id,
                retry_strategy=self.sdk.retry.NoneRetryStrategy())
            if int(response.headers.get("content-length", -1)) > maximum:
                raise CloudError("OBJECT_TOO_LARGE")
            body = response.data.raw.read(maximum + 1)
            if not isinstance(body, bytes) or len(body) > maximum:
                raise CloudError("OBJECT_TOO_LARGE")
            return body
        except CloudError:
            raise
        except Exception as exc:
            raise _error("OBJECT_READ_FAILED", exc) from None
        finally:
            if response is not None and hasattr(response.data, "close"):
                response.data.close()


class OciQueue:
    def __init__(self, sdk, client, config):
        self.sdk, self.client, self.config = sdk, client, config
        self.queue_id = config["queue_id"]

    def publish(self, content):
        if not isinstance(content, dict) or set(content) != {"type", "schema_version", "job_id", "scope_id"}:
            raise CloudError("QUEUE_PAYLOAD_SCOPE_INVALID")
        encoded = json.dumps(content, separators=(",", ":"), allow_nan=False)
        if len(encoded) > 2048:
            raise CloudError("QUEUE_PAYLOAD_TOO_LARGE")
        models = self.sdk.queue.models
        details = models.PutMessagesDetails(messages=[models.PutMessagesDetailsEntry(content=encoded)])
        try:
            response = self.client.put_messages(self.queue_id, details,
                retry_strategy=self.sdk.retry.NoneRetryStrategy())
            messages = response.data.messages
            if len(messages) != 1 or messages[0].id is None:
                raise CloudError("QUEUE_PUBLISH_RESPONSE_INVALID")
            return str(messages[0].id)
        except CloudError:
            raise
        except Exception as exc:
            raise _error("QUEUE_PUBLISH_FAILED", exc) from None

    def receive(self, visibility_seconds):
        try:
            response = self.client.get_messages(self.queue_id, limit=1,
                visibility_in_seconds=visibility_seconds,
                timeout_in_seconds=self.config["queue_poll_seconds"],
                retry_strategy=self.sdk.retry.NoneRetryStrategy())
            messages = response.data.messages
            if not messages:
                return None
            if len(messages) != 1 or not isinstance(messages[0].receipt, str) or not messages[0].receipt:
                raise CloudError("QUEUE_RECEIVE_RESPONSE_INVALID")
            message = messages[0]
            try:
                if not isinstance(message.content, str) or len(message.content) > 2048:
                    raise ValueError("message size")
                content = json.loads(message.content, object_pairs_hook=unique_pairs,
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("number")))
            except Exception:
                content = None  # Preserve receipt so the workflow can defer/quarantine.
            return {"receipt": message.receipt, "content": content, "message_id": str(message.id)}
        except CloudError:
            raise
        except Exception as exc:
            raise _error("QUEUE_RECEIVE_FAILED", exc) from None

    def acknowledge(self, receipt):
        try:
            self.client.delete_message(self.queue_id, receipt,
                retry_strategy=self.sdk.retry.NoneRetryStrategy())
        except Exception as exc:
            raise _error("QUEUE_ACK_FAILED", exc) from None

    def defer(self, receipt, visibility_seconds):
        try:
            self.client.update_message(self.queue_id, receipt,
                self.sdk.queue.models.UpdateMessageDetails(visibility_in_seconds=visibility_seconds),
                retry_strategy=self.sdk.retry.NoneRetryStrategy())
        except Exception as exc:
            raise _error("QUEUE_DEFER_FAILED", exc) from None


def verify_resource_scope(sdk, objects, admin, config):
    """Read the exact configured resources, never discover arbitrary compartments."""
    try:
        bucket = objects.get_bucket(config["object_storage_namespace"], config["object_bucket"],
            retry_strategy=sdk.retry.NoneRetryStrategy()).data
        if (bucket.compartment_id != config["compartment_id"] or bucket.name != config["object_bucket"]
                or bucket.namespace != config["object_storage_namespace"]
                or bucket.public_access_type != "NoPublicAccess" or bucket.versioning != "Enabled"):
            raise CloudError("BUCKET_SCOPE_OR_PRIVACY_MISMATCH")
        queue = admin.get_queue(config["queue_id"], retry_strategy=sdk.retry.NoneRetryStrategy()).data
        endpoint = str(queue.messages_endpoint).rstrip("/")
        if (queue.id != config["queue_id"] or queue.compartment_id != config["compartment_id"]
                or queue.lifecycle_state != "ACTIVE" or not valid_messages_endpoint(endpoint)
                or endpoint != config["queue_messages_endpoint"].rstrip("/")):
            raise CloudError("QUEUE_SCOPE_OR_ENDPOINT_MISMATCH")
    except CloudError:
        raise
    except Exception as exc:
        raise _error("CLOUD_RESOURCE_SCOPE_CHECK_FAILED", exc) from None


def database_factory(config, driver):
    """Return a short-lived connection factory; called only after execution gates."""
    database = config["database"]
    def connect():
        try:
            addresses = {result[4][0] for result in socket.getaddrinfo(database["host"], 1522, type=socket.SOCK_STREAM)}
            if not addresses:
                raise CloudError("DATABASE_DNS_EMPTY")
            for value in addresses:
                address = ipaddress.ip_address(value)
                if not address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
                    raise CloudError("DATABASE_PRIVATE_ADDRESS_REQUIRED")
            wallet = os.environ[database["wallet_dir_env"]]
            if not Path(wallet).is_dir():
                raise CloudError("DATABASE_WALLET_DIRECTORY_REQUIRED")
            connection = driver.connect(user="KK_RUNTIME", password=os.environ[database["password_env"]],
                host=database["host"], port=1522, service_name=database["service_name"], protocol="tcps",
                wallet_location=wallet, wallet_password=os.environ[database["wallet_password_env"]],
                ssl_server_dn_match=True, tcp_connect_timeout=10, retry_count=0)
            connection.call_timeout = 30000
            return connection
        except CloudError:
            raise
        except Exception:
            raise CloudError("DATABASE_CONNECTION_FAILED") from None
    return connect


def build_live(config: dict, *, execute: bool):
    """Only production entry point for optional imports, auth and resource reads."""
    if execute is not True:
        raise CloudError("EXPLICIT_EXECUTE_REQUIRED")
    config = validate_config(config)
    try:
        import oci
        import oracledb
    except ImportError:
        raise CloudError("OPTIONAL_CLOUD_DEPENDENCIES_REQUIRED") from None
    try:
        auth = config["oci_auth"]
        if auth["mode"] == "instance_principal":
            sdk_config = {"region": config["region"]}
            options = {"signer": oci.auth.signers.InstancePrincipalsSecurityTokenSigner()}
        else:
            sdk_config = oci.config.from_file(auth["config_path"], auth["profile"])
            sdk_config["region"] = config["region"]
            options = {}
        options.update(timeout=(config["network_connect_timeout_seconds"], config["network_read_timeout_seconds"]),
                       retry_strategy=oci.retry.NoneRetryStrategy())
        storage = oci.object_storage.ObjectStorageClient(sdk_config, **options)
        admin = oci.queue.QueueAdminClient(sdk_config, **options)
        verify_resource_scope(oci, storage, admin, config)
        queue = oci.queue.QueueClient(sdk_config, service_endpoint=config["queue_messages_endpoint"], **options)
        from .oracle import OracleRepository
        repository = OracleRepository(database_factory(config, oracledb), oracledb)
        return repository, OciObjects(oci, storage, config), OciQueue(oci, queue, config)
    except CloudError:
        raise
    except Exception:
        raise CloudError("CLOUD_CLIENT_INITIALIZATION_FAILED") from None
