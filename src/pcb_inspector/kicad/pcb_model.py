"""High-level object model for KiCad PCB layouts (.kicad_pcb)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pcb_inspector.core.exceptions import ProjectParsingError
from pcb_inspector.kicad.sexpr_parser import find_all, find_first, get_value, parse_sexpr


class Pad(BaseModel):
    """Pcb footprint pad."""

    model_config = ConfigDict(frozen=True)

    number: str
    pad_type: str = "smd"  # smd, thru_hole, connect, np_thru_hole
    shape: str = "rect"  # rect, roundrect, circle, oval
    at_x: float = 0.0
    at_y: float = 0.0
    size_w: float = 0.0
    size_h: float = 0.0
    layers: list[str] = Field(default_factory=list)
    net_num: int = 0
    net_name: str = ""

    @property
    def is_power_or_gnd(self) -> bool:
        n = self.net_name.upper()
        return any(
            p in n
            for p in (
                "VCC",
                "VDD",
                "VBUS",
                "VBAT",
                "+3V3",
                "+5V",
                "+12V",
                "3V3",
                "5V",
                "GND",
                "AGND",
                "DGND",
            )
        )

    @property
    def is_ground(self) -> bool:
        return "GND" in self.net_name.upper()

    @property
    def is_power(self) -> bool:
        return self.is_power_or_gnd and not self.is_ground


class Footprint(BaseModel):
    """Pcb component footprint."""

    refdes: str
    name: str = ""
    layer: str = "F.Cu"
    at_x: float = 0.0
    at_y: float = 0.0
    at_rotation: float = 0.0
    pads: list[Pad] = Field(default_factory=list)

    @property
    def is_ic(self) -> bool:
        """True if component looks like an integrated circuit."""
        return self.refdes.startswith("U") or self.refdes.startswith("IC")

    @property
    def is_capacitor(self) -> bool:
        return self.refdes.startswith("C")

    @property
    def is_inductor(self) -> bool:
        return self.refdes.startswith("L")

    @property
    def is_diode(self) -> bool:
        return self.refdes.startswith("D")

    def get_pad(self, number: str) -> Pad | None:
        for p in self.pads:
            if p.number == number:
                return p
        return None


class TrackSegment(BaseModel):
    """Straight trace segment on copper layer."""

    start_x: float
    start_y: float
    end_x: float
    end_y: float
    width: float
    layer: str
    net_num: int
    net_name: str = ""

    @property
    def length(self) -> float:
        return math.hypot(self.end_x - self.start_x, self.end_y - self.start_y)


class Via(BaseModel):
    """Drilled via connecting copper layers."""

    x: float
    y: float
    size: float
    drill: float
    layers: tuple[str, str]
    net_num: int
    net_name: str = ""


class Zone(BaseModel):
    """Copper polygon zone (e.g. ground plane)."""

    net_num: int
    net_name: str
    layer: str
    points: list[tuple[float, float]] = Field(default_factory=list)


class PcbBoard(BaseModel):
    """Complete in-memory representation of a KiCad PCB layout."""

    file_path: str
    version: str = ""
    generator: str = ""
    thickness: float = 1.6
    nets: dict[int, str] = Field(default_factory=dict)
    footprints: dict[str, Footprint] = Field(default_factory=dict)
    tracks: list[TrackSegment] = Field(default_factory=list)
    vias: list[Via] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)

    def get_footprint(self, refdes: str) -> Footprint | None:
        return self.footprints.get(refdes)

    def get_tracks_by_net(self, net_name: str) -> list[TrackSegment]:
        target = net_name.upper()
        return [t for t in self.tracks if t.net_name.upper() == target]

    def get_capacitors_on_net(self, net_name: str) -> list[Footprint]:
        caps: list[Footprint] = []
        for fp in self.footprints.values():
            if fp.is_capacitor:
                for pad in fp.pads:
                    if pad.net_name.upper() == net_name.upper():
                        caps.append(fp)
                        break
        return caps


def _parse_coords(node: list[Any] | None) -> tuple[float, float, float]:
    """Parse (at x y [rot]) into (x, y, rot)."""
    if not node or not isinstance(node, list):
        return (0.0, 0.0, 0.0)
    x = float(node[1]) if len(node) > 1 else 0.0
    y = float(node[2]) if len(node) > 2 else 0.0
    rot = float(node[3]) if len(node) > 3 else 0.0
    return (x, y, rot)


def load_pcb_board(file_path: Path | str) -> PcbBoard:
    """Load and parse a KiCad .kicad_pcb file into a PcbBoard model."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"PCB file not found: {path}")

    text = path.read_text(encoding="utf-8")
    parsed = parse_sexpr(text)
    if not parsed or not isinstance(parsed, list) or parsed[0] != "kicad_pcb":
        raise ProjectParsingError(f"File {path} is not a valid kicad_pcb S-expression.")

    # Version & Generator
    ver_node = find_first(parsed, "version")
    version = str(get_value(ver_node, 1, ""))
    gen_node = find_first(parsed, "generator")
    generator = str(get_value(gen_node, 1, ""))

    # Board Thickness
    thickness = 1.6
    gen_section = find_first(parsed, "general")
    if gen_section:
        thick_node = find_first(gen_section, "thickness")
        if thick_node:
            try:
                thickness = float(get_value(thick_node, 1, 1.6))
            except (ValueError, TypeError):
                thickness = 1.6

    # Nets
    nets: dict[int, str] = {}
    for n in find_all(parsed, "net"):
        try:
            num = int(n[1])
            name = str(n[2]) if len(n) > 2 else ""
            nets[num] = name
        except (ValueError, IndexError):
            continue

    # Footprints
    footprints: dict[str, Footprint] = {}
    for fp_node in find_all(parsed, "footprint"):
        fp_name = str(get_value(fp_node, 1, ""))
        at_node = find_first(fp_node, "at")
        at_x, at_y, at_rot = _parse_coords(at_node)
        layer_node = find_first(fp_node, "layer")
        fp_layer = str(get_value(layer_node, 1, "F.Cu"))

        # RefDes from properties
        refdes = ""
        for prop in find_all(fp_node, "property"):
            if len(prop) >= 3 and prop[1] == "Reference":
                refdes = str(prop[2])
                break

        if not refdes:
            # Fallback for KiCad 6/7 (fp_text reference ...)
            for fp_text in find_all(fp_node, "fp_text"):
                if len(fp_text) >= 3 and fp_text[1] == "reference":
                    refdes = str(fp_text[2])
                    break

        if not refdes:
            refdes = f"UNKNOWN_{len(footprints) + 1}"

        # Pads
        pads: list[Pad] = []
        for pad_node in find_all(fp_node, "pad"):
            if len(pad_node) < 4:
                continue
            pad_num = str(pad_node[1])
            pad_type = str(pad_node[2])
            pad_shape = str(pad_node[3])

            pad_at_node = find_first(pad_node, "at")
            rel_x, rel_y, _ = _parse_coords(pad_at_node)

            # Global pad coordinates considering component position and rotation
            rad = math.radians(at_rot)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            pad_x = at_x + (rel_x * cos_a - rel_y * sin_a)
            pad_y = at_y + (rel_x * sin_a + rel_y * cos_a)

            # Size
            size_w, size_h = 0.0, 0.0
            size_node = find_first(pad_node, "size")
            if size_node and len(size_node) >= 3:
                try:
                    size_w = float(size_node[1])
                    size_h = float(size_node[2])
                except (ValueError, TypeError):
                    pass

            # Net
            net_num = 0
            net_name = ""
            net_node = find_first(pad_node, "net")
            if net_node and len(net_node) >= 2:
                try:
                    net_num = int(net_node[1])
                    net_name = nets.get(net_num, str(net_node[2]) if len(net_node) > 2 else "")
                except (ValueError, IndexError):
                    pass

            # Layers
            layers_node = find_first(pad_node, "layers")
            pad_layers = [str(lay) for lay in layers_node[1:]] if layers_node else []

            pads.append(
                Pad(
                    number=pad_num,
                    pad_type=pad_type,
                    shape=pad_shape,
                    at_x=pad_x,
                    at_y=pad_y,
                    size_w=size_w,
                    size_h=size_h,
                    layers=pad_layers,
                    net_num=net_num,
                    net_name=net_name,
                )
            )

        footprints[refdes] = Footprint(
            refdes=refdes,
            name=fp_name,
            layer=fp_layer,
            at_x=at_x,
            at_y=at_y,
            at_rotation=at_rot,
            pads=pads,
        )

    # Tracks
    tracks: list[TrackSegment] = []
    for segment in find_all(parsed, "segment"):
        start_node = find_first(segment, "start")
        end_node = find_first(segment, "end")
        width_node = find_first(segment, "width")
        layer_node = find_first(segment, "layer")
        net_node = find_first(segment, "net")

        if not start_node or not end_node:
            continue

        sx = float(start_node[1]) if len(start_node) > 1 else 0.0
        sy = float(start_node[2]) if len(start_node) > 2 else 0.0
        ex = float(end_node[1]) if len(end_node) > 1 else 0.0
        ey = float(end_node[2]) if len(end_node) > 2 else 0.0
        w = float(width_node[1]) if width_node and len(width_node) > 1 else 0.25
        lay = str(layer_node[1]) if layer_node and len(layer_node) > 1 else "F.Cu"

        net_n = 0
        net_lbl = ""
        if net_node and len(net_node) > 1:
            try:
                net_n = int(net_node[1])
                net_lbl = nets.get(net_n, "")
            except ValueError:
                pass

        tracks.append(
            TrackSegment(
                start_x=sx,
                start_y=sy,
                end_x=ex,
                end_y=ey,
                width=w,
                layer=lay,
                net_num=net_n,
                net_name=net_lbl,
            )
        )

    # Vias
    vias: list[Via] = []
    for via_node in find_all(parsed, "via"):
        at_node = find_first(via_node, "at")
        vx, vy, _ = _parse_coords(at_node)
        size_node = find_first(via_node, "size")
        drill_node = find_first(via_node, "drill")
        layer_node = find_first(via_node, "layers")
        net_node = find_first(via_node, "net")

        v_size = float(size_node[1]) if size_node and len(size_node) > 1 else 0.8
        v_drill = float(drill_node[1]) if drill_node and len(drill_node) > 1 else 0.4

        v_layers = ("F.Cu", "B.Cu")
        if layer_node and len(layer_node) >= 3:
            v_layers = (str(layer_node[1]), str(layer_node[2]))

        v_net_n = 0
        v_net_lbl = ""
        if net_node and len(net_node) > 1:
            try:
                v_net_n = int(net_node[1])
                v_net_lbl = nets.get(v_net_n, "")
            except ValueError:
                pass

        vias.append(
            Via(
                x=vx,
                y=vy,
                size=v_size,
                drill=v_drill,
                layers=v_layers,
                net_num=v_net_n,
                net_name=v_net_lbl,
            )
        )

    # Zones
    zones: list[Zone] = []
    for z_node in find_all(parsed, "zone"):
        net_node = find_first(z_node, "net")
        layer_node = find_first(z_node, "layer")
        z_net_num = 0
        z_net_name = ""
        if net_node and len(net_node) > 1:
            try:
                z_net_num = int(net_node[1])
                z_net_name = nets.get(z_net_num, "")
            except ValueError:
                pass
        z_layer = str(layer_node[1]) if layer_node and len(layer_node) > 1 else "B.Cu"

        poly_node = find_first(z_node, "polygon")
        pts_list: list[tuple[float, float]] = []
        if poly_node:
            pts_node = find_first(poly_node, "pts")
            if pts_node:
                for xy_node in find_all(pts_node, "xy"):
                    if len(xy_node) >= 3:
                        try:
                            pts_list.append((float(xy_node[1]), float(xy_node[2])))
                        except ValueError:
                            continue

        zones.append(Zone(net_num=z_net_num, net_name=z_net_name, layer=z_layer, points=pts_list))

    return PcbBoard(
        file_path=str(path),
        version=version,
        generator=generator,
        thickness=thickness,
        nets=nets,
        footprints=footprints,
        tracks=tracks,
        vias=vias,
        zones=zones,
    )
