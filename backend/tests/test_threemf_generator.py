"""Unit tests for backend.core.threemf_generator."""
import zipfile
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from backend.core.threemf_generator import ThreeMFGenerator


# --- ASCII STL helpers for tests ---

def _write_ascii_stl(path: Path, vertices: list, triangles: list) -> None:
    """Write a minimal ASCII STL (one triangle per facet)."""
    lines = ["solid test"]
    for tri in triangles:
        i, j, k = tri
        a, b, c = vertices[i], vertices[j], vertices[k]
        # Normal from cross product (simplified: assume upward)
        lines.append("  facet normal 0 0 1")
        lines.append(f"    outer loop")
        lines.append(f"      vertex {a[0]} {a[1]} {a[2]}")
        lines.append(f"      vertex {b[0]} {b[1]} {b[2]}")
        lines.append(f"      vertex {c[0]} {c[1]} {c[2]}")
        lines.append(f"    endloop")
        lines.append("  endfacet")
    lines.append("endsolid test")
    path.write_text("\n".join(lines))


class TestThreeMFGeneratorComputeBoundingBox:
    def test_empty_vertices(self):
        gen = ThreeMFGenerator()
        result = gen._compute_bounding_box([])
        assert result["min"] == [0, 0, 0]
        assert result["max"] == [0, 0, 0]
        assert result["size"] == [0, 0, 0]
        assert result["center"] == [0, 0, 0]

    def test_single_vertex(self):
        gen = ThreeMFGenerator()
        result = gen._compute_bounding_box([[1.0, 2.0, 3.0]])
        assert result["min"] == [1.0, 2.0, 3.0]
        assert result["max"] == [1.0, 2.0, 3.0]
        assert result["size"] == [0, 0, 0]
        assert result["center"] == [1.0, 2.0, 3.0]

    def test_cube(self):
        gen = ThreeMFGenerator()
        vertices = [
            [0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0],
            [0, 0, 5], [10, 0, 5], [10, 10, 5], [0, 10, 5],
        ]
        result = gen._compute_bounding_box(vertices)
        assert result["min"] == [0, 0, 0]
        assert result["max"] == [10, 10, 5]
        assert result["size"] == [10, 10, 5]
        assert result["center"] == [5.0, 5.0, 2.5]


class TestThreeMFGeneratorParseStl:
    def test_valid_ascii_stl(self, tmp_path):
        gen = ThreeMFGenerator()
        stl = tmp_path / "part.stl"
        vertices = [[0, 0, 0], [1, 0, 0], [0.5, 1, 0]]
        triangles = [[0, 1, 2]]
        _write_ascii_stl(stl, vertices, triangles)
        result = gen._parse_stl(stl)
        assert result is not None
        assert len(result["vertices"]) == 3
        assert result["triangles"] == [[0, 1, 2]]

    def test_missing_file_returns_none(self):
        gen = ThreeMFGenerator()
        result = gen._parse_stl(Path("/nonexistent/file.stl"))
        assert result is None

    def test_welds_shared_vertices_repeated_by_stl_facets(self, tmp_path):
        stl = tmp_path / "88646_1.stl"
        vertices = [
            [0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [0, 0, 1],
        ]
        triangles = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]
        _write_ascii_stl(stl, vertices, triangles)

        mesh = ThreeMFGenerator()._parse_stl(stl)

        assert mesh is not None
        assert len(mesh["vertices"]) == 4
        assert len(mesh["triangles"]) == 4
        assert all(len(set(triangle)) == 3 for triangle in mesh["triangles"])


class TestThreeMFGeneratorPackParts:
    def test_single_part_fits(self):
        gen = ThreeMFGenerator(part_spacing=2)
        mesh = {
            "vertices": [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0],
                        [0, 0, 5], [10, 0, 5], [10, 10, 5], [0, 10, 5]],
            "triangles": [[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]],
        }
        bbox = gen._compute_bounding_box(mesh["vertices"])
        part_meshes = [
            {"mesh_data": mesh, "ldraw_id": "3005", "bbox": bbox, "quantity": 1, "color_rgb": None}
        ]
        placements = gen._pack_parts(part_meshes, plate_width=220, plate_depth=220, plate_height=250)
        assert placements is not None
        assert len(placements) == 1
        assert "translation" in placements[0]
        assert placements[0]["ldraw_id"] == "3005"

    def test_quantity_two(self):
        gen = ThreeMFGenerator(part_spacing=2)
        mesh = {
            "vertices": [[0, 0, 0], [5, 0, 0], [5, 5, 0], [0, 5, 0]],
            "triangles": [[0, 1, 2], [0, 2, 3]],
        }
        bbox = gen._compute_bounding_box(mesh["vertices"])
        part_meshes = [
            {"mesh_data": mesh, "ldraw_id": "x", "bbox": bbox, "quantity": 2, "color_rgb": "FF0000"}
        ]
        placements = gen._pack_parts(part_meshes, plate_width=100, plate_depth=100, plate_height=250)
        assert placements is not None
        assert len(placements) == 2


class TestThreeMFGeneratorCreate3mf:
    def test_create_3mf_file(self, tmp_path):
        gen = ThreeMFGenerator()
        mesh = {
            "vertices": [[0, 0, 0], [1, 0, 0], [0.5, 1, 0]],
            "triangles": [[0, 1, 2]],
        }
        placements = [
            {
                "mesh_data": mesh,
                "translation": [0.0, 0.0, 0.0],
                "ldraw_id": "3005",
                "instance_num": 1,
                "color_rgb": "808080",
            }
        ]
        out = tmp_path / "out.3mf"
        assert gen._create_3mf_file(placements, out) is True
        assert out.exists()
        with zipfile.ZipFile(out, "r") as zf:
            names = set(zf.namelist())
            project_settings = json.loads(zf.read("Metadata/project_settings.config"))
            model_settings = ET.fromstring(zf.read("Metadata/model_settings.config"))
            model = ET.fromstring(zf.read("3D/3dmodel.model"))
        assert "[Content_Types].xml" in names
        assert "_rels/.rels" in names
        assert "3D/3dmodel.model" in names
        assert "3D/Objects/object_1.model" in names
        assert "3D/_rels/3dmodel.model.rels" in names
        assert "Metadata/slice_info.config" in names
        assert model.attrib["requiredextensions"] == "p"
        model_ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ns = {"m": model_ns}
        assert model.find("./m:metadata[@name='Application']", ns).text.startswith("BambuStudio-")
        assert len(model_settings.findall("./object")) == 1
        assert len(model_settings.findall("./assemble/assemble_item")) == 2
        assert [child.tag for child in model_settings] == [
            "object", "plate", "assemble"
        ]
        assert project_settings["printer_model"] == "Bambu Lab A1"
        assert project_settings["print_settings_id"] == "0.20mm PETG LEGO"
        assert project_settings["filament_settings_id"] == [
            "Generic PETG @BBL A1 - LEGO"
        ] * 4

    def test_3mf_model_has_mesh_and_build_item(self, tmp_path):
        gen = ThreeMFGenerator()
        mesh = {
            "vertices": [[0, 0, 0], [1, 0, 0], [0.5, 1, 0]],
            "triangles": [[0, 1, 2]],
        }
        placements = [
            {
                "mesh_data": mesh,
                "translation": [1.0, 2.0, 0.0],
                "ldraw_id": "88646",
                "instance_num": 1,
                "color_rgb": None,
            }
        ]
        out = tmp_path / "out.3mf"
        assert gen._create_3mf_file(placements, out) is True
        with zipfile.ZipFile(out, "r") as zf:
            raw = zf.read("3D/3dmodel.model").decode("utf-8")
            mesh_raw = zf.read("3D/Objects/object_1.model").decode("utf-8")
            model_settings = ET.fromstring(zf.read("Metadata/model_settings.config"))
        root = ET.fromstring(raw)
        mesh_root = ET.fromstring(mesh_raw)
        model_ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ns = {"m": model_ns}
        vertices = mesh_root.findall(".//m:vertices", ns)
        build_items = root.findall(".//m:item", ns)
        assert len(vertices) >= 1
        assert len(build_items) >= 1
        assert model_settings.find("./plate/model_instance/metadata[@key='object_id']").attrib["value"] == "2"
        object_name = model_settings.find("./object/metadata[@key='name']")
        assert object_name.attrib["value"] == "88646_1.stl"

    def test_3mf_preserves_multiple_build_plates(self, tmp_path):
        gen = ThreeMFGenerator(part_spacing=1)
        mesh = {
            "vertices": [[0, 0, 0], [4, 0, 0], [0, 4, 1]],
            "triangles": [[0, 1, 2]],
        }
        bbox = {
            "min": [0, 0, 0],
            "max": [4, 4, 1],
            "size": [4, 4, 1],
            "center": [2, 2, 0.5],
        }
        placements = gen._pack_parts(
            [{"mesh_data": mesh, "ldraw_id": "3005", "quantity": 2, "bbox": bbox}],
            plate_width=10,
            plate_depth=6,
            plate_height=10,
        )
        assert placements is not None
        assert [placement["plate_index"] for placement in placements] == [0, 1]
        assert placements[0]["translation"][1] == placements[1]["translation"][1]

        out = tmp_path / "multi-plate.3mf"
        assert gen._create_3mf_file(placements, out) is True
        with zipfile.ZipFile(out, "r") as zf:
            model_settings = ET.fromstring(zf.read("Metadata/model_settings.config"))
            model = ET.fromstring(zf.read("3D/3dmodel.model"))
        plates = model_settings.findall("./plate")
        assert len(plates) == 2
        assert [
            plate.find("./metadata[@key='plater_id']").attrib["value"]
            for plate in plates
        ] == ["1", "2"]
        assert all(len(plate.findall("./model_instance")) == 1 for plate in plates)
        assert len(model_settings.findall("./assemble/assemble_item")) == 4
        model_ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        build_items = model.findall(
            "./m:build/m:item",
            {"m": model_ns},
        )
        first_transform = [float(value) for value in build_items[0].get("transform").split()]
        second_transform = [float(value) for value in build_items[1].get("transform").split()]
        assert abs(second_transform[9] - first_transform[9] - 307.2) < 1e-6
        assert second_transform[10] == first_transform[10]

    def test_each_color_gets_a_plate_and_filament_slot(self, tmp_path):
        gen = ThreeMFGenerator(part_spacing=1)
        mesh = {
            "vertices": [[0, 0, 0], [4, 0, 0], [0, 4, 1]],
            "triangles": [[0, 1, 2]],
        }
        bbox = gen._compute_bounding_box(mesh["vertices"])
        colors = ["FF0000", "00FF00", "0000FF", "FFFF00", "FF00FF"]
        part_meshes = [
            {
                "mesh_data": mesh,
                "ldraw_id": f"part-{index}",
                "quantity": 1,
                "bbox": bbox,
                "color_rgb": color,
            }
            for index, color in enumerate(colors)
        ]
        placements = gen._pack_parts(part_meshes, 100, 100, 100)

        assert placements is not None
        assert [placement["plate_index"] for placement in placements] == list(
            range(len(colors))
        )
        out = tmp_path / "color-plates.3mf"
        assert gen._create_3mf_file(placements, out)
        with zipfile.ZipFile(out, "r") as zf:
            model_settings = ET.fromstring(zf.read("Metadata/model_settings.config"))
            project_settings = json.loads(zf.read("Metadata/project_settings.config"))
            model = ET.fromstring(zf.read("3D/3dmodel.model"))

        plates = model_settings.findall("./plate")
        assert len(plates) == len(colors)
        assert all(len(plate.findall("./model_instance")) == 1 for plate in plates)
        assert len(model_settings.findall("./assemble/assemble_item")) == 2 * len(colors)
        assert [
            plate.find("./metadata[@key='plater_name']").attrib["value"].split("#")[-1]
            for plate in plates
        ] == colors
        assert project_settings["filament_colour"] == [f"#{color}" for color in colors]
        assert len(project_settings["filament_settings_id"]) == len(colors)
        assert [
            obj.find("./metadata[@key='extruder']").attrib["value"]
            for obj in model_settings.findall("./object")
        ] == [str(index) for index in range(1, len(colors) + 1)]
        model_ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        build_items = model.findall(
            "./m:build/m:item",
            {"m": model_ns},
        )
        transforms = [
            [float(value) for value in item.get("transform").split()]
            for item in build_items
        ]
        assert abs(transforms[2][9] - transforms[0][9] - 614.4) < 1e-6
        assert abs(transforms[3][10] - transforms[0][10] + 307.2) < 1e-6
        assert abs(transforms[4][9] - transforms[0][9] - 307.2) < 1e-6
        assert abs(transforms[4][10] - transforms[0][10] + 307.2) < 1e-6


class TestThreeMFGeneratorGenerate3mf:
    def test_generate_3mf_integration(self, tmp_path):
        stl = tmp_path / "part.stl"
        _write_ascii_stl(stl, [[0, 0, 0], [10, 0, 0], [5, 10, 0]], [[0, 1, 2]])
        gen = ThreeMFGenerator(part_spacing=2)
        parts = [(str(stl), "3005", 1)]
        out = tmp_path / "project.3mf"
        assert gen.generate_3mf(parts, plate_width=220, plate_depth=220, plate_height=250, output_path=out) is True
        assert out.exists()

    def test_generate_3mf_with_color(self, tmp_path):
        stl = tmp_path / "part.stl"
        _write_ascii_stl(stl, [[0, 0, 0], [10, 0, 0], [5, 10, 0]], [[0, 1, 2]])
        gen = ThreeMFGenerator(part_spacing=2)
        parts = [(str(stl), "3005", 1, "FF5500")]
        out = tmp_path / "project.3mf"
        assert gen.generate_3mf(parts, plate_width=220, plate_depth=220, plate_height=250, output_path=out) is True
        assert out.exists()

    def test_generated_3mf_uses_repaired_stl_topology_and_name(self, tmp_path):
        stl = tmp_path / "88646_1.stl"
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
        triangles = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]
        _write_ascii_stl(stl, vertices, triangles)
        out = tmp_path / "project.3mf"

        gen = ThreeMFGenerator()
        assert gen.generate_3mf(
            [(str(stl), "88646", 1)],
            plate_width=220,
            plate_depth=220,
            plate_height=250,
            output_path=out,
        )

        with zipfile.ZipFile(out, "r") as archive:
            mesh = ET.fromstring(archive.read("3D/Objects/object_1.model"))
            model_settings = ET.fromstring(
                archive.read("Metadata/model_settings.config")
            )
        model_ns = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
        ns = {"m": model_ns}
        assert len(mesh.findall(".//m:vertex", ns)) == 4
        assert len(mesh.findall(".//m:triangle", ns)) == 4
        assert (
            model_settings.find("./object/metadata[@key='name']").attrib["value"]
            == "88646_1.stl"
        )
