"""Custom exceptions hierarchy for pcb-inspector."""


class PcbInspectorError(Exception):
    """Base exception for all pcb-inspector errors."""


class KiCadCliNotFoundError(PcbInspectorError):
    """Raised when kicad-cli cannot be found in PATH or configured location."""


class KiCadCliExecutionError(PcbInspectorError):
    """Raised when kicad-cli exits with an unexpected error."""


class ProjectParsingError(PcbInspectorError):
    """Raised when a KiCad schematic or PCB file cannot be parsed."""


class RuleExecutionError(PcbInspectorError):
    """Raised when a specific verification rule fails unexpectedly."""


class VisionReviewError(PcbInspectorError):
    """Raised when visual multimodal analysis encounters an error or API failure."""
