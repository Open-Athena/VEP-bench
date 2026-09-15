"""Export standalone task panels from the combined blog SVG.

Regenerate with: uv run projects/explorer/scripts/export_task_schematics.py
Edit the combined SVG to keep the blog and standalone drawings consistent.
"""

from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

SVG = "http://www.w3.org/2000/svg"
PANELS = {
    "fitness-task": "sge",
    "expression-task": "satmut-mpra",
    "splicing-task": "opensplice-snv",
}


def main() -> None:
    web = Path(__file__).resolve().parents[1] / "web"
    source = web / "blog/introducing-vep-bench/tasks-overview.svg"
    output = web / "tasks/schematics"
    document = ET.parse(source).getroot()
    definitions = document.find(f"{{{SVG}}}defs")
    if definitions is None:
        raise ValueError("Combined schematic has no shared SVG definitions")

    ET.register_namespace("", SVG)
    output.mkdir(parents=True, exist_ok=True)
    for panel_id, slug in PANELS.items():
        panel = document.find(f".//{{{SVG}}}g[@id='{panel_id}']")
        if panel is None:
            raise ValueError(f"Combined schematic is missing {panel_id}")
        background = panel.find(f"{{{SVG}}}rect")
        if background is None:
            raise ValueError(f"Panel {panel_id} has no background bounds")

        bounds = background.attrib
        standalone = ET.Element(
            f"{{{SVG}}}svg",
            {
                "width": bounds["width"],
                "height": bounds["height"],
                "viewBox": " ".join(bounds[key] for key in ("x", "y", "width", "height")),
                "role": "img",
                "aria-labelledby": "title description",
            },
        )
        ET.SubElement(standalone, f"{{{SVG}}}title", id="title").text = panel.attrib["aria-label"]
        ET.SubElement(standalone, f"{{{SVG}}}desc", id="description").text = ". ".join(
            "".join(label.itertext()) for label in panel.iter(f"{{{SVG}}}text")
        )
        standalone.append(
            ET.Comment(
                " Generated from blog/introducing-vep-bench/tasks-overview.svg by "
                "uv run projects/explorer/scripts/export_task_schematics.py. "
                "Edit the combined SVG, then regenerate. "
            )
        )
        standalone.append(deepcopy(definitions))
        # The panel's own coordinates and background define the crop; page-level
        # placement transforms in the combined figure are deliberately omitted.
        standalone.append(deepcopy(panel))
        ET.indent(standalone, space="  ")
        destination = output / f"{slug}.svg"
        destination.write_text(
            ET.tostring(standalone, encoding="unicode") + "\n", encoding="utf-8", newline="\n"
        )
        print(destination)


if __name__ == "__main__":
    main()
