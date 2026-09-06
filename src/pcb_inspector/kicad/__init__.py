"""KiCad integration modules: CLI wrapper, report parsers, and S-Expression parser."""

from pcb_inspector.kicad.cli_wrapper import KiCadCli
from pcb_inspector.kicad.report_parser import (
    extract_coordinates,
    extract_nets,
    extract_refdes,
    map_kicad_severity,
    parse_drc_json,
    parse_erc_json,
)
from pcb_inspector.kicad.sexpr_parser import (
    find_all,
    find_first,
    get_value,
    parse_sexpr,
    tokenize,
)

__all__ = [
    "KiCadCli",
    "extract_coordinates",
    "extract_nets",
    "extract_refdes",
    "find_all",
    "find_first",
    "get_value",
    "map_kicad_severity",
    "parse_drc_json",
    "parse_erc_json",
    "parse_sexpr",
    "tokenize",
]
