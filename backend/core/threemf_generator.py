"""3MF file generation with bin packing for 3D printing."""
import json
import logging
import math
import os
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# 3MF Materials Extension namespace for color
NS_P = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
NS_BAMBU = "http://schemas.bambulab.com/package/2021"
NS_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

PRINTER_MODEL = "Bambu Lab A1"
PRINTER_PRESET = "Bambu Lab A1 0.4 nozzle"
PROCESS_PRESET = "0.20mm PETG LEGO"
FILAMENT_PRESET = "Generic PETG @BBL A1 - LEGO"
BAMBU_VERSION = "02.08.02.61"
BAMBU_PLATE_GRID_PITCH = 307.2
BAMBU_SETTINGS_TEMPLATE = Path(__file__).with_name("bambu_a1_petg_project_settings.json")

ET.register_namespace("", NS_CORE)
ET.register_namespace("p", NS_P)
ET.register_namespace("BambuStudio", NS_BAMBU)


class ThreeMFGenerator:
    """Generate 3MF files with parts arranged on build plate using bin packing."""

    @staticmethod
    def _normalize_color(color: Optional[str]) -> str:
        normalized = (color or "808080").strip().lstrip("#").upper()
        if len(normalized) != 6 or any(
            char not in "0123456789ABCDEF" for char in normalized
        ):
            return "808080"
        return normalized
    
    def __init__(self, part_spacing: int = 2):
        """Initialize generator.
        
        Args:
            part_spacing: Minimum spacing between parts in mm
        """
        self.part_spacing = part_spacing
    
    def generate_3mf(
        self,
        parts: List[Tuple[Any, ...]],  # (stl_path, ldraw_id, quantity) or (..., color_rgb)
        plate_width: int,
        plate_depth: int,
        plate_height: int,
        output_path: Path
    ) -> bool:
        """Generate 3MF file with parts arranged on build plate(s) using bin packing.
        
        Args:
            parts: (stl_path, ldraw_id, quantity) or (stl_path, ldraw_id, quantity, color_rgb).
                   color_rgb optional, e.g. "FF5500" (hex without #).
            plate_width: Build plate width in mm
            plate_depth: Build plate depth in mm
            plate_height: Build plate height in mm (for reference; packing uses width/depth)
            output_path: Where to save the 3MF file
        
        Returns:
            True if successful, False if parts don't fit or error occurs
        """
        try:
            part_meshes = []
            for t in parts:
                # FIX: Ensure index access, [1], [2] is declared properly
                stl_path, ldraw_id, quantity = t[0], t[1], t[2]
                color_rgb = (t[3].strip().lstrip("#") or None) if len(t) > 3 and t[3] else None
                if color_rgb and len(color_rgb) != 6:
                    color_rgb = None
                mesh_data = self._parse_stl(Path(stl_path))
                if mesh_data:
                    bbox = self._compute_bounding_box(mesh_data['vertices'])
                    part_meshes.append({
                        'mesh_data': mesh_data,
                        'ldraw_id': ldraw_id,
                        'bbox': bbox,
                        'quantity': quantity,
                        'color_rgb': color_rgb,
                    })
            if not part_meshes:
                logger.error("No valid meshes to pack")
                return False
            placements = self._pack_parts(part_meshes, plate_width, plate_depth, plate_height)
            if not placements:
                logger.error("Parts do not fit on build plate(s)")
                return False
            return self._create_3mf_file(placements, output_path)
            
        except Exception as e:
            logger.error(f"Error generating 3MF: {e}")
            return False
    
    def _parse_stl(self, stl_path: Path) -> Optional[Dict]:
        """Parse ASCII STL file to extract vertices and triangles.
        
        Returns:
            Dict with 'vertices' and 'triangles' or None
        """
        try:
            vertices = []
            triangles = []
            vertex_indices = {}
            current_vertices = []
            
            with open(stl_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    
                    if line.startswith('vertex'):
                        parts = line.split()
                        if len(parts) >= 4:
                            vertex = [float(parts[1]), float(parts[2]), float(parts[3])]
                            vertex_key = tuple(vertex)
                            vertex_index = vertex_indices.get(vertex_key)
                            if vertex_index is None:
                                vertex_index = len(vertices)
                                vertices.append(vertex)
                                vertex_indices[vertex_key] = vertex_index
                            current_vertices.append(vertex_index)
                    
                    elif line.startswith('endfacet'):
                        if len(current_vertices) == 3:
                            triangles.append(current_vertices[:])
                        current_vertices = []
            
            return {
                'vertices': vertices,
                'triangles': triangles
            }
            
        except Exception as e:
            logger.error(f"Error parsing STL {stl_path}: {e}")
            return None
    
    def _compute_bounding_box(self, vertices: List[List[float]]) -> Dict:
        """Compute axis-aligned bounding box of vertices.
        
        Returns:
            Dict with 'min', 'max', 'size', 'center'
        """
        if not vertices:
            return {'min': [0, 0, 0], 'max': [0, 0, 0], 'size': [0, 0, 0], 'center': [0, 0, 0]}
        
        min_x = min(v[0] for v in vertices)
        min_y = min(v[1] for v in vertices)
        min_z = min(v[2] for v in vertices)
        max_x = max(v[0] for v in vertices)
        max_y = max(v[1] for v in vertices)
        max_z = max(v[2] for v in vertices)
        
        size_x = max_x - min_x
        size_y = max_y - min_y
        size_z = max_z - min_z
        
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        center_z = (min_z + max_z) / 2
        
        return {
            'min': [min_x, min_y, min_z],
            'max': [max_x, max_y, max_z],
            'size': [size_x, size_y, size_z],
            'center': [center_x, center_y, center_z]
        }
    
    def _pack_parts(self, part_meshes: List[Dict], plate_width: int, plate_depth: int, plate_height: int) -> Optional[List[Dict]]:
        """Pack parts onto build plate using shelf algorithm.
        
        Args:
            part_meshes: List of part mesh dictionaries
            plate_width: Build plate width in mm
            plate_depth: Build plate depth in mm
            plate_height: Build plate height in mm (for reference; packing uses width/depth)
        
        Returns:
            List of placements with mesh_data and translation, or None if doesn't fit
        """
        placements = []
        
        # Keep each color on its own plate; split it further only if it does not fit.
        instances_by_color = {}
        for part in part_meshes:
            color = self._normalize_color(part.get('color_rgb'))
            color_instances = instances_by_color.setdefault(color, [])
            for i in range(part['quantity']):
                color_instances.append({
                    'mesh_data': part['mesh_data'],
                    'ldraw_id': part['ldraw_id'],
                    'bbox': part['bbox'],
                    'instance_num': i + 1,
                    'color_rgb': color,
                })
        
        next_plate_index = 0

        for color_instances in instances_by_color.values():
            color_instances.sort(
                key=lambda item: item['bbox']['size'][0] * item['bbox']['size'][1],
                reverse=True,
            )
            local_plate_index = 0
            shelves = []
            current_y = 0.0

            for instance in color_instances:
                bbox = instance['bbox']
                width, depth, height = bbox['size']
                width_with_spacing = width + self.part_spacing
                depth_with_spacing = depth + self.part_spacing

                placed = False
                for shelf in shelves:
                    if (
                        shelf['width_remaining'] >= width_with_spacing
                        and current_y + depth_with_spacing <= plate_depth
                    ):
                        x_pos = shelf['x']
                        y_pos = shelf['y']
                        translation = [
                            x_pos + width / 2 - bbox['center'][0],
                            y_pos + depth / 2 - bbox['center'][1],
                            -bbox['min'][2],
                        ]
                        placements.append({
                            'mesh_data': instance['mesh_data'],
                            'translation': translation,
                            'plate_index': next_plate_index + local_plate_index,
                            'ldraw_id': instance['ldraw_id'],
                            'instance_num': instance['instance_num'],
                            'color_rgb': instance['color_rgb'],
                        })
                        shelf['x'] += width_with_spacing
                        shelf['width_remaining'] -= width_with_spacing
                        shelf['height'] = max(shelf['height'], height)
                        placed = True
                        break

                if not placed:
                    if shelves and current_y + depth_with_spacing > plate_depth:
                        local_plate_index += 1
                        current_y = 0.0
                        shelves = []

                    x_pos = 0
                    y_pos = current_y
                    translation = [
                        x_pos + width / 2 - bbox['center'][0],
                        y_pos + depth / 2 - bbox['center'][1],
                        -bbox['min'][2],
                    ]
                    placements.append({
                        'mesh_data': instance['mesh_data'],
                        'translation': translation,
                        'plate_index': next_plate_index + local_plate_index,
                        'ldraw_id': instance['ldraw_id'],
                        'instance_num': instance['instance_num'],
                        'color_rgb': instance['color_rgb'],
                    })
                    shelves.append({
                        'y': current_y,
                        'height': height,
                        'x': width_with_spacing,
                        'width_remaining': plate_width - width_with_spacing,
                    })
                    current_y += height + self.part_spacing

            next_plate_index += local_plate_index + 1

        logger.info(
            f"Packed {len(placements)} parts onto {next_plate_index} build plate(s)"
        )
        return placements
    
    def _create_3mf_file(
        self,
        placements: List[Dict],
        output_path: Path
    ) -> bool:
        """Create 3MF ZIP file with proper structure.
        
        Args:
            placements: List of part placements with mesh_data and translation
            output_path: Where to save the 3MF file
        
        Returns:
            True if successful
        """
        try:
            # Create temporary directory for 3MF structure
            import tempfile
            
            with tempfile.TemporaryDirectory() as tmpdir:
                tmppath = Path(tmpdir)

                (tmppath / "3D" / "Objects").mkdir(parents=True)
                (tmppath / "3D" / "_rels").mkdir()
                (tmppath / "_rels").mkdir()
                (tmppath / "Metadata").mkdir()

                self._create_content_types(tmppath)
                self._create_rels(tmppath)
                self._create_model_file(tmppath, placements)
                self._create_project_settings(tmppath, placements)
                self._create_slice_info(tmppath)

                with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(tmppath):
                        root_path = Path(root)
                        for file in files:
                            file_path = root_path / file
                            zf.write(file_path, file_path.relative_to(tmppath))
                return True
                
        except Exception as e:
            logger.error(f"Error creating 3MF file: {e}")
            return False
    
    def _create_content_types(self, base_path: Path):
        """Create [Content_Types].xml file."""
        root = ET.Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
        ET.SubElement(root, "Default", Extension="rels", ContentType="application/vnd.openxmlformats-package.relationships+xml")
        ET.SubElement(root, "Default", Extension="model", ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml")
        
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        tree.write(base_path / "[Content_Types].xml", encoding="utf-8", xml_declaration=True)
    
    def _create_rels(self, base_path: Path):
        """Create _rels/.rels file."""
        root = ET.Element("Relationships", xmlns="http://schemas.openxmlformats.org/package/2006/relationships")
        ET.SubElement(root, "Relationship",
                     Target="/3D/3dmodel.model",
                     Id="rel-1",
                     Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel")
        
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        tree.write(base_path / "_rels" / ".rels", encoding="utf-8", xml_declaration=True)

    def _create_project_settings(self, base_path: Path, placements: List[Dict]):
        """Add the validated A1 PETG project settings used by Bambu Studio."""
        with BAMBU_SETTINGS_TEMPLATE.open(encoding="utf-8") as settings_file:
            settings = json.load(settings_file)

        colors = []
        for placement in placements:
            color = self._normalize_color(placement.get("color_rgb"))
            if color not in colors:
                colors.append(color)

        colors.extend([colors[0] if colors else "808080"] * (4 - len(colors)))
        filament_count = len(colors)
        settings.update({
            "printer_model": PRINTER_MODEL,
            "printer_settings_id": PRINTER_PRESET,
            "printer_variant": "0.4",
            "nozzle_diameter": ["0.4"],
            "print_settings_id": PROCESS_PRESET,
            "default_print_profile": "0.20mm Standard @BBL A1",
            "default_filament_profile": [FILAMENT_PRESET],
            "filament_settings_id": [FILAMENT_PRESET] * filament_count,
            "filament_type": ["PETG"] * filament_count,
            "filament_vendor": ["Generic"] * filament_count,
            "filament_extruder_variant": ["Direct Drive Standard"] * filament_count,
            "filament_flow_ratio": ["0.96"] * filament_count,
            "nozzle_temperature_range_low": ["240"] * filament_count,
            "nozzle_temperature_range_high": ["250"] * filament_count,
            "filament_colour": [f"#{color}" for color in colors],
        })
        for key, value in tuple(settings.items()):
            if (
                isinstance(value, list)
                and len(value) == 4
                and (key.startswith("filament_") or key.startswith("nozzle_temperature_range_"))
            ):
                settings[key] = value + [value[-1]] * (filament_count - 4)
        settings["filament_self_index"] = [
            str(index) for index in range(1, filament_count + 1)
        ]
        settings["filament_colour"] = [f"#{color}" for color in colors]
        settings["filament_multi_colour"] = [f"#{color}" for color in colors]
        settings["default_filament_colour"] = [f"#{color}" for color in colors]

        config_dir = base_path / "Metadata"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "project_settings.config").write_text(
            json.dumps(settings, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )

    def _create_slice_info(self, base_path: Path):
        config = ET.Element("config")
        header = ET.SubElement(config, "header")
        ET.SubElement(
            header, "header_item", key="X-BBL-Client-Type", value="slicer"
        )
        ET.SubElement(
            header, "header_item", key="X-BBL-Client-Version", value=BAMBU_VERSION
        )
        ET.indent(config, space="  ")
        ET.ElementTree(config).write(
            base_path / "Metadata" / "slice_info.config",
            encoding="UTF-8",
            xml_declaration=True,
        )

    def _create_model_file(self, base_path: Path, placements: List[Dict]):
        """Write Bambu's Production Extension model and its linked mesh resources."""
        root = ET.Element("model", {
            "unit": "millimeter",
            "xml:lang": "en-US",
            "xmlns": NS_CORE,
            "xmlns:BambuStudio": NS_BAMBU,
            "xmlns:p": NS_P,
            "requiredextensions": "p",
        })
        self._add_metadata(root, "Application", f"BambuStudio-{BAMBU_VERSION}")
        self._add_metadata(root, "BambuStudio:3mfVersion", "1")
        self._add_metadata(root, "CreationDate", date.today().isoformat())

        resources = ET.SubElement(root, "resources")
        build = ET.SubElement(root, "build", {"p:UUID": str(uuid4())})
        relationships = ET.Element(
            "Relationships",
            xmlns=NS_REL,
        )
        model_settings = ET.Element("config")
        plate_indices = sorted({
            max(0, int(placement.get("plate_index", 0)))
            for placement in placements
        })
        # Bambu lays plate origins out in a square-ish grid at this fixed pitch.
        plate_grid_columns = math.ceil(math.sqrt(max(plate_indices, default=0) + 1))
        plates = {}
        plate_colors = {}
        for placement in placements:
            plate_index = max(0, int(placement.get("plate_index", 0)))
            plate_colors.setdefault(
                plate_index, self._normalize_color(placement.get("color_rgb"))
            )
        for plate_index in plate_indices:
            plate = ET.Element("plate")
            plates[plate_index] = plate
            plate_color = plate_colors.get(plate_index, "808080")
            for key, value in (
                ("plater_id", str(plate_index + 1)),
                ("plater_name", f"Plate {plate_index + 1} - #{plate_color}"),
                ("locked", "false"),
                ("filament_map_mode", "Auto For Flush"),
                ("filament_maps", "1 1 1 1"),
                ("filament_volume_maps", "0 0 0 0"),
            ):
                ET.SubElement(plate, "metadata", key=key, value=value)

        identity_transform = "1 0 0 0 1 0 0 0 1 0 0 0"
        assemble = ET.Element("assemble")
        unique_colors = []
        for candidate in placements:
            candidate_color = self._normalize_color(candidate.get("color_rgb"))
            if candidate_color not in unique_colors:
                unique_colors.append(candidate_color)

        for idx, placement in enumerate(placements):
            object_id = 2 + idx * 2
            mesh_object_id = object_id - 1
            mesh_data = placement['mesh_data']
            vertices = mesh_data["vertices"]
            if not vertices:
                raise ValueError(f"Placement {idx} has no vertices")
            mins = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
            maxs = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
            center = [(mins[axis] + maxs[axis]) / 2 for axis in range(3)]
            translation = placement["translation"]
            plate_index = max(0, int(placement.get("plate_index", 0)))
            plate_column = plate_index % plate_grid_columns
            plate_row = plate_index // plate_grid_columns
            placed_center = [
                translation[0] + center[0] + plate_column * BAMBU_PLATE_GRID_PITCH,
                translation[1] + center[1] - plate_row * BAMBU_PLATE_GRID_PITCH,
                translation[2] + center[2],
            ]
            matrix = (
                f"1 0 0 0 1 0 0 0 1 "
                f"{placed_center[0]:.9g} {placed_center[1]:.9g} "
                f"{placed_center[2]:.9g}"
            )

            color = self._normalize_color(placement.get("color_rgb"))
            extruder = str(unique_colors.index(color) + 1)

            path = f"/3D/Objects/object_{idx + 1}.model"
            outer_uuid = str(uuid4())
            component_uuid = str(uuid4())
            obj = ET.SubElement(
                resources,
                "object",
                {
                    "id": str(object_id),
                    "p:UUID": outer_uuid,
                    "type": "model",
                },
            )
            components = ET.SubElement(obj, "components")
            ET.SubElement(
                components,
                "component",
                {
                    "p:path": path,
                    "objectid": str(mesh_object_id),
                    "p:UUID": component_uuid,
                    "transform": identity_transform,
                },
            )

            mesh_root = ET.Element("model", {
                "unit": "millimeter",
                "xml:lang": "en-US",
                "xmlns": NS_CORE,
                "xmlns:BambuStudio": NS_BAMBU,
                "xmlns:p": NS_P,
                "requiredextensions": "p",
            })
            self._add_metadata(mesh_root, "BambuStudio:3mfVersion", "1")
            mesh_resources = ET.SubElement(mesh_root, "resources")
            mesh_obj = ET.SubElement(
                mesh_resources,
                "object",
                {
                    "id": str(mesh_object_id),
                    "p:UUID": str(uuid4()),
                    "type": "model",
                },
            )
            mesh_element = ET.SubElement(mesh_obj, "mesh")
            vertices_element = ET.SubElement(mesh_element, "vertices")
            triangles_element = ET.SubElement(mesh_element, "triangles")
            for vertex in vertices:
                ET.SubElement(
                    vertices_element,
                    "vertex",
                    x=f"{vertex[0] - center[0]:.9g}",
                    y=f"{vertex[1] - center[1]:.9g}",
                    z=f"{vertex[2] - center[2]:.9g}",
                )
            for triangle in mesh_data["triangles"]:
                ET.SubElement(
                    triangles_element,
                    "triangle",
                    v1=str(triangle[0]),
                    v2=str(triangle[1]),
                    v3=str(triangle[2]),
                )
            ET.SubElement(mesh_root, "build")
            mesh_path = base_path / "3D" / "Objects" / f"object_{idx + 1}.model"
            self._write_xml(mesh_root, mesh_path)

            ET.SubElement(
                relationships,
                "Relationship",
                Target=path,
                Id=f"rel-{idx + 1}",
                Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel",
            )
            ET.SubElement(
                build,
                "item",
                {
                    "objectid": str(object_id),
                    "p:UUID": str(uuid4()),
                    "transform": matrix,
                    "printable": "1",
                },
            )
            ET.SubElement(
                assemble,
                "assemble_item",
                {
                    "object_id": str(object_id),
                    "instance_id": "0",
                    "transform": matrix,
                    "offset": "0 0 0",
                },
            )
            ET.SubElement(
                assemble,
                "assemble_item",
                {
                    "object_id": str(object_id),
                    "volume_id": "0",
                    "transform": identity_transform,
                },
            )

            part_name = (
                f"{placement.get('ldraw_id', 'part')}_"
                f"{placement.get('instance_num', idx + 1)}.stl"
            )
            part = ET.SubElement(
                model_settings, "object", id=str(object_id)
            )
            ET.SubElement(part, "metadata", key="name", value=part_name)
            ET.SubElement(part, "metadata", key="extruder", value=extruder)
            ET.SubElement(
                part, "metadata", face_count=str(len(mesh_data["triangles"]))
            )
            part_uuid = str(uuid4())
            part_settings = ET.SubElement(
                part,
                "part",
                id=str(mesh_object_id),
                subtype="normal_part",
                uuid=part_uuid,
            )
            ET.SubElement(part_settings, "metadata", key="name", value=part_name)
            ET.SubElement(
                part_settings,
                "metadata",
                key="matrix",
                value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1",
            )
            ET.SubElement(
                part_settings, "metadata", key="source_file", value="BrickGen"
            )
            ET.SubElement(
                part_settings, "metadata", key="source_object_id", value="0"
            )
            ET.SubElement(
                part_settings, "metadata", key="source_volume_id", value="0"
            )
            for axis, value in zip("xyz", center):
                ET.SubElement(
                    part_settings,
                    "metadata",
                    key=f"source_offset_{axis}",
                    value=f"{value:.9g}",
                )
            ET.SubElement(
                part_settings,
                "mesh_stat",
                face_count=str(len(mesh_data["triangles"])),
                edges_fixed="0",
                degenerate_facets="0",
                facets_removed="0",
                facets_reversed="0",
                backwards_edges="0",
            )
            model_instance = ET.SubElement(plates[plate_index], "model_instance")
            ET.SubElement(
                model_instance, "metadata", key="object_id", value=str(object_id)
            )
            ET.SubElement(
                model_instance, "metadata", key="instance_id", value="0"
            )
            ET.SubElement(
                model_instance,
                "metadata",
                key="identify_id",
                value=str(idx + 1),
            )

        for plate_index in plate_indices:
            model_settings.append(plates[plate_index])
        model_settings.append(assemble)
        self._write_xml(root, base_path / "3D" / "3dmodel.model")
        self._write_xml(
            relationships,
            base_path / "3D" / "_rels" / "3dmodel.model.rels",
        )
        self._write_xml(
            model_settings,
            base_path / "Metadata" / "model_settings.config",
        )

    @staticmethod
    def _add_metadata(parent: ET.Element, name: str, value: str) -> None:
        ET.SubElement(parent, "metadata", name=name).text = value

    @staticmethod
    def _write_xml(root: ET.Element, path: Path) -> None:
        ET.indent(root, space=" ")
        ET.ElementTree(root).write(
            path,
            encoding="UTF-8",
            xml_declaration=True,
        )
