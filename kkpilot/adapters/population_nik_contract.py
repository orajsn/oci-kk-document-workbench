"""NIK visual-alternative contract with independent No/name population evidence."""
from . import nik_alternatives_contract as base

VERSION = base.VERSION
PROMPT_VERSION = 'kk-population-nik-read-1.0'
ROLES = ('row_identity_column', 'nik_column')
MEMBER_FIELDS = base.MEMBER_FIELDS
NikReadError = base.NikReadError
canonical, sha, need = base.canonical, base.sha, base.need
build_schema, google_response_schema = base.build_schema, base.google_response_schema
validate_wire, decode_json, extract_wire = base.validate_wire, base.decode_json, base.extract_wire


def validate_views(views):
    need(type(views) is list and len(views) == 2, 'NIK_COLUMN_COUNT')
    need(tuple(view.get('name') for view in views if type(view) is dict) == ROLES,
         'NIK_COLUMN_ROLES')
    return base.validate_views([
        {'name': role, 'shared_y_extent': view.get('shared_y_extent')}
        for role, view in zip(base.ROLES, views)])


def build_prompt(views):
    """Use only source roles and geometry, never caller metadata or other readings."""
    validate_views(views)
    legacy = [{'name': role, 'shared_y_extent': view['shared_y_extent']}
              for role, view in zip(base.ROLES, views)]
    # Reuse all raw-value and bounded glyph-alternative rules unchanged.
    anchor = 'Read each printed member No. once,'
    tail = base.build_prompt(legacy).partition(anchor)
    need(bool(tail[1]), 'NIK_POPULATION_BASE_PROMPT_CHANGED')
    return (
        'Read the printed member No. and NIK entries from these TWO crops of the '
        'SAME Indonesian Kartu Keluarga image. row_identity_column shows the '
        'printed No. plus visible name snippets; nik_column shows the NIK '
        'column. Both crops cover exactly the same vertical extent and retain '
        'original source pixels. Coarse boundaries come from fallible geometry. '
        'No expected row counts, supplied member numbers, previous readings or '
        'reference answers are provided. Use the actual printed No. together '
        'with visible name presence to identify people; a printed No. alone '
        'does not establish a populated person row. Name snippets may stop at '
        'the NIK-column boundary and need not contain the complete name. Use '
        'visible name presence only as a population cue; do not transcribe '
        'names or infer identifier characters from a name. Ignore headings '
        'and column-number captions such as 1 and 2. Ignore a blank template '
        'row when its name and target cells are both blank or contain only '
        "a standalone '-' placeholder, even if a printed No. exists. If a "
        'name is visibly present but its target cell is blank or contains only '
        "a standalone '-', retain the person row with nik_raw null and empty "
        'visual_candidates and ambiguous_positions arrays. Read EVERY populated '
        'member row exactly once. Retain EVERY additional row containing real '
        'name or target data, including target data on a row whose name is '
        'blank or unreadable. Never suppress a data-bearing row to match a '
        'presumed population or count. '
        + tail[1] + tail[2])


def expand_candidate(wire):
    result = base.expand_candidate(wire)
    result['audit'].update(
        prompt_version=PROMPT_VERSION, wire_contract_version=VERSION,
        input_mode='two_shared_y_population_source_columns',
        identity_context='printed_no_and_visible_name_presence',
        name_transcription_requested=False, row_population_is_model_reported=True,
        population_filtering_performed=False, all_data_bearing_rows_required=True)
    return result
