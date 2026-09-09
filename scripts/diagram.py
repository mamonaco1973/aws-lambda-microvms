"""Generate the editable draw.io architecture and matching SVG/PNG."""
import os
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
NODES = [
    ("browser", "Browser|No local server", 20, 390, 200, 90, "#dae8fc"),
    ("web", "S3 HTTPS frontend|HTML + JS + config", 280, 80, 220, 90, "#d5e8d4"),
    ("cognito", "Cognito Hosted UI|Code + PKCE login", 280, 240, 220, 90, "#e1d5e7"),
    ("gateway", "API Gateway|JWT + required scope", 280, 410, 220, 90, "#e1d5e7"),
    ("controller", "Controller Lambda|Submit / poll status", 560, 410, 220, 90, "#ffe6cc"),
    ("ddb", "DynamoDB|IDs, observations, jobs|No interpreter restore", 560, 640, 220, 100, "#dae8fc"),
    ("sqs", "SQS FIFO|Serialized operations", 840, 410, 210, 90, "#f8cecc"),
    ("worker", "Worker Lambda|MicroVM tokens stay here", 1110, 410, 240, 90, "#ffe6cc"),
    ("control", "Lambda MicroVM API|Lifecycle + tokens", 1110, 630, 240, 90, "#ffe6cc"),
    ("artifact", "Private S3 source|Dockerfile + Python", 560, 80, 220, 90, "#d5e8d4"),
    ("image", "Initialized image|Memory + disk snapshot", 840, 80, 250, 90, "#ffe6cc"),
    ("alice", "Alice MicroVM|Interpreter + RAM + files|HTTPS :8080 / hooks :8081", 820, 800, 260, 100, "#d5e8d4"),
    ("bob", "Bob MicroVM|Interpreter + RAM + files|HTTPS :8080 / hooks :8081", 1110, 800, 260, 100, "#dae8fc"),
]
EDGES = [
    ("browser", "web", "assets", [(120, 390), (120, 125), (280, 125)]),
    ("browser", "cognito", "login", [(180, 390), (180, 285), (280, 285)]),
    ("browser", "gateway", "access token", [(220, 455), (280, 455)]),
    ("gateway", "controller", "", [(500, 455), (560, 455)]),
    ("controller", "sqs", "enqueue", [(780, 455), (840, 455)]),
    ("sqs", "worker", "", [(1050, 455), (1110, 455)]),
    ("controller", "ddb", "metadata", [(650, 500), (650, 640)]),
    ("worker", "ddb", "save result", [(1110, 480), (1085, 480), (1085, 690), (780, 690)]),
    ("worker", "control", "AWS SDK", [(1240, 500), (1240, 630)]),
    ("worker", "alice", "VM-authenticated HTTPS", [(1170, 500), (1170, 565), (960, 565), (960, 800)]),
    ("worker", "bob", "HTTPS", [(1350, 455), (1385, 455), (1385, 760), (1240, 760), (1240, 800)]),
    ("artifact", "image", "build", [(780, 125), (840, 125)]),
    ("controller", "control", "GetMicrovm only", [(720, 500), (720, 600), (1045, 600), (1045, 750), (1200, 750), (1200, 720)]),
]
LABELS = {"assets": (125, 250), "login": (185, 345), "access token": (240, 393),
          "enqueue": (790, 397), "metadata": (655, 565), "save result": (840, 680),
          "AWS SDK": (1245, 575), "VM-authenticated HTTPS": (810, 783),
          "HTTPS": (1285, 783), "build": (790, 112), "GetMicrovm only": (725, 590)}


def main():
    mx = ET.Element("mxfile", host="app.diagrams.net")
    diagram = ET.SubElement(mx, "diagram", id="microvms", name="AWS Lambda MicroVMs")
    model = ET.SubElement(diagram, "mxGraphModel", page="1", pageWidth="1420", pageHeight="960", background="#ffffff")
    root = ET.SubElement(model, "root")
    ET.SubElement(root, "mxCell", id="0")
    ET.SubElement(root, "mxCell", id="1", parent="0")
    cloud = ET.SubElement(root, "mxCell", id="cloud", parent="1", vertex="1",
        value="AWS Cloud - provisioned with Terraform", style="fillColor=none;strokeColor=#232F3E;verticalAlign=top;align=left;spacing=12;fontSize=16;")
    ET.SubElement(cloud, "mxGeometry", x="250", y="20", width="1150", height="910", attrib={"as": "geometry"})
    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg", width="1420", height="960", viewBox="0 0 1420 960")
    ET.SubElement(svg, "rect", width="1420", height="960", fill="white")
    defs = ET.SubElement(svg, "defs")
    marker = ET.SubElement(defs, "marker", id="arrow", markerWidth="10", markerHeight="7", refX="9", refY="3.5", orient="auto")
    ET.SubElement(marker, "polygon", points="0 0, 10 3.5, 0 7", fill="#555")
    ET.SubElement(svg, "rect", x="250", y="20", width="1150", height="910", fill="none", stroke="#232f3e")

    def label(x, y, text, size=13, anchor="start"):
        ET.SubElement(svg, "text", x=str(x), y=str(y), attrib={"font-family": "Arial", "font-size": str(size), "text-anchor": anchor}).text = text

    label(270, 48, "AWS Cloud - provisioned with Terraform", 17)
    label(1110, 110, "Both sessions launch from", 14)
    label(1110, 133, "the initialized image above.", 14)
    label(270, 953, "Control-plane polling does not wake a VM. Runtime VMs have no AWS execution role. Session state is ephemeral.", 13)
    for i, (source, target, text, points) in enumerate(EDGES):
        cell = ET.SubElement(root, "mxCell", id=f"edge{i}", parent="1", edge="1", source=source, target=target,
            value=text, style="endArrow=classic;html=0;fontSize=11;labelBackgroundColor=#ffffff;")
        geo = ET.SubElement(cell, "mxGeometry", relative="1", attrib={"as": "geometry"})
        if len(points) > 2:
            array = ET.SubElement(geo, "Array", attrib={"as": "points"})
            for x, y in points[1:-1]:
                ET.SubElement(array, "mxPoint", x=str(x), y=str(y))
        ET.SubElement(svg, "polyline", points=" ".join(f"{x},{y}" for x, y in points), fill="none", stroke="#555", attrib={"marker-end": "url(#arrow)"})
        if text:
            x, y = LABELS[text]
            label(x, y, text, 11)
    for key, text, x, y, width, height, fill in NODES:
        cell = ET.SubElement(root, "mxCell", id=key, parent="1", vertex="1", value=text.replace("|", "\n"),
            style=f"rounded=0;whiteSpace=wrap;html=0;fontSize=14;fillColor={fill};strokeColor=#666666;")
        ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(width), height=str(height), attrib={"as": "geometry"})
        ET.SubElement(svg, "rect", x=str(x), y=str(y), width=str(width), height=str(height), fill=fill, stroke="#666")
        lines = text.split("|")
        for i, line in enumerate(lines):
            label(x+width/2, y+height/2+(i-(len(lines)-1)/2)*22+5, line, 14, "middle")
    ET.indent(mx)
    ET.ElementTree(mx).write(ROOT / "aws-lambda-microvms.drawio", encoding="utf-8", xml_declaration=True)
    markup = ET.tostring(svg, encoding="unicode")
    (ROOT / "aws-lambda-microvms.svg").write_text(markup)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get("PLAYWRIGHT_CHANNEL", "msedge" if os.name == "nt" else None), headless=True)
        page = browser.new_page(viewport={"width": 1420, "height": 960}, device_scale_factor=2)
        page.set_content('<body style="margin:0">' + markup + '</body>')
        page.screenshot(path=str(ROOT / "aws-lambda-microvms.png"))
        browser.close()
    print("NOTE: Architecture diagram generated.")


if __name__ == "__main__":
    main()
