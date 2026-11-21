"""Configuration validation only. No cloud client or network request."""
from pathlib import Path
import json
import unittest
from unittest.mock import patch
from kkpilot.cloud.config import ACK,issues
from kkpilot.adapters import oci_gemini as gem


class ConfigTest(unittest.TestCase):
    def test_gemini_example_can_be_completed_without_editing_source(self):
        root=Path(__file__).resolve().parents[1]
        cfg=json.loads((root/"kkpilot/cloud/config.example.json").read_bytes())
        self.assertTrue(issues(cfg))
        compartment="ocid1.compartment."+ "example"
        profile="ocid1.generativeairoutingprofile."+ "example"
        cfg.update(enabled=True,ack=ACK,data_processing_authorized=True,spending_authorized=True,
                   authorization_reference="synthetic validation",compartment_id=compartment,
                   object_bucket="example",object_storage_namespace="example",
                   queue_id="ocid1.queue."+"ap-batam-1.example",
                   queue_messages_endpoint="https://example.messaging.ap-batam-1.oci.oraclecloud.com")
        cfg["database"].update(id="ocid1.autonomousdatabase."+"ap-batam-1.example",
                               host="example.adb.ap-batam-1.oraclecloud.com",
                               service_name="example_low",private_endpoint_verified=True)
        cfg["oci_adapter"].update(allow_live_calls=True,data_processing_authorized=True,spending_authorized=True,
                   authorization_reference=cfg["authorization_reference"],dataset_id="synthetic",
                   vision_api_compatibility_verified=True,google_americas_processing_authorized=True,
                   compartment_id=compartment,routing_profile_id=profile,approved_image_sha256s=["0"*64])
        with patch.object(gem,"COMPARTMENT",compartment),patch.object(gem,"PROFILE",profile):
            self.assertEqual(issues(cfg),[])
            cfg["oci_adapter"]["routing_profile_id"]=""
            self.assertTrue(issues(cfg))
