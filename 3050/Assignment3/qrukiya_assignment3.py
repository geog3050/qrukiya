"""Assignment 3: Spatial Overlays and Areal Interpolation.
Complete three functions using ArcPy and pathlib.Path. Prepare and project
copies before analysis; preserve inputs and avoid hardcoded paths or values.
Check input paths, geometry types, fields, coordinate systems, and output names.
Use try/except handling to catch and report errors.
Find and download census boundaries, a census/ACS numeric attribute, and roads (or another dataset with line geometry).
"""


from pathlib import Path
import arcpy
import uuid


def hawkid():
    return ["Quazi Umme Rukiya", "qrukiya"]


# Problem 1 (20 points)
def prepare_data(input_fc, output_geodatabase, out_coordinate_system,
                 join_table=None, join_field=None, value_field=None):
    """Create or reuse the geodatabase; import and project one feature dataset.
    Use the input base name and return the prepared feature class path.
    For polygons, import a CSV's ID attribute and one numeric value
    and join that value using the join_field that . Supply all three join
    arguments together. Preserve inputs and reject existing output names.
    """
    output_fc = None
    created_output = False
    temporary_table = None
    try:
        input_path = Path(input_fc)
        gdb_path = Path(output_geodatabase)
        if not arcpy.Exists(str(input_path)):
            raise FileNotFoundError(f"Input feature class not found: {input_path}")

        desc = arcpy.Describe(str(input_path))
        if desc.shapeType not in ("Polygon", "Polyline", "Point", "Multipoint"):
            raise ValueError(f"Unsupported input geometry: {desc.shapeType}")
        if desc.spatialReference is None or desc.spatialReference.name == "Unknown":
            raise ValueError("Input has an unknown coordinate system.")

        target_sr = (arcpy.SpatialReference(out_coordinate_system)
                     if isinstance(out_coordinate_system, int)
                     else arcpy.SpatialReference(int(out_coordinate_system))
                     if str(out_coordinate_system).isdigit()
                     else out_coordinate_system)
        if not hasattr(target_sr, "type") or target_sr.type != "Projected":
            raise ValueError("Output coordinate system must be projected.")
        if abs(target_sr.metersPerUnit - 1.0) > 1e-8:
            raise ValueError("Output coordinate system must use meters.")

        join_args = (join_table, join_field, value_field)
        if any(v is not None for v in join_args):
            if not all(v is not None for v in join_args):
                raise ValueError("Provide join_table, join_field and value_field together.")
            if desc.shapeType != "Polygon":
                raise ValueError("The optional population join requires polygons.")
            if not arcpy.Exists(str(join_table)):
                raise FileNotFoundError(f"Join table not found: {join_table}")
            source_fields = {f.name.lower(): f for f in arcpy.ListFields(str(input_path))}
            table_fields = {f.name.lower(): f for f in arcpy.ListFields(str(join_table))}
            key = join_field.lower()
            val = value_field.lower()
            if key not in source_fields or key not in table_fields:
                raise ValueError(f"Join field {join_field!r} must exist in both inputs.")
            # Census IDs may be text in shapefiles but numeric in ArcGIS CSV views.
            # Normalize in temporary copies; never change the supplied files.
            source_key_type = source_fields[key].type
            table_key_type = table_fields[key].type
            numeric_types = ("SmallInteger", "Integer", "BigInteger", "Single", "Double")
            if source_key_type != table_key_type and not (
                source_key_type in ("String", *numeric_types)
                and table_key_type in ("String", *numeric_types)
            ):
                raise ValueError("Join ID fields cannot be normalized safely.")
            if val not in table_fields:
                raise ValueError(f"Value field not found in join table: {value_field}")
            if table_fields[val].type not in ("SmallInteger", "Integer", "BigInteger", "Single", "Double"):
                raise ValueError("Population/value field must be numeric.")
            if val in source_fields:
                raise ValueError(f"Output already has a field named {value_field!r}.")

        if gdb_path.suffix.lower() != ".gdb":
            raise ValueError("output_geodatabase must end in .gdb")
        if not arcpy.Exists(str(gdb_path)):
            if not gdb_path.parent.is_dir():
                raise FileNotFoundError(f"Geodatabase parent folder not found: {gdb_path.parent}")
            arcpy.management.CreateFileGDB(str(gdb_path.parent), gdb_path.name)
        elif arcpy.Describe(str(gdb_path)).dataType != "Workspace":
            raise ValueError(f"Not a geodatabase workspace: {gdb_path}")

        name = arcpy.ValidateTableName(input_path.stem, str(gdb_path))
        output_fc = str(gdb_path / name)
        if arcpy.Exists(output_fc):
            raise FileExistsError(f"Output already exists: {output_fc}")

        arcpy.management.Project(str(input_path), output_fc, target_sr)
        created_output = True
        if join_table is not None:
            # Normalize GEOID-like keys to text without leading zeroes for comparison.
            # For example, '029157...' and 29157... become the same join key.
            def normalize_id(value):
                if value is None:
                    return None
                key_text = str(value).strip()
                if not key_text:
                    return None
                return (key_text.lstrip("0") or "0") if key_text.isdigit() else key_text

            source_ids = []
            with arcpy.da.SearchCursor(output_fc, [join_field]) as rows:
                for (raw_id,) in rows:
                    source_ids.append(normalize_id(raw_id))
            if None in source_ids or len(set(source_ids)) != len(source_ids):
                raise ValueError("Source polygon IDs must be nonempty and unique.")

            table_ids = set()
            with arcpy.da.SearchCursor(str(join_table), [join_field, value_field]) as rows:
                for raw_id, pop in rows:
                    normalized = normalize_id(raw_id)
                    if normalized is None or normalized in table_ids:
                        raise ValueError("Join table IDs must be nonempty and unique.")
                    table_ids.add(normalized)
                    if pop is None or pop < 0:
                        raise ValueError("Population counts must be present and nonnegative.")
            if not set(source_ids).issubset(table_ids):
                raise ValueError("Some source polygon IDs have no matching population record.")

            temporary_table = f"memory\\hw3_join_{uuid.uuid4().hex[:12]}"
            arcpy.management.CopyRows(str(join_table), temporary_table)
            temp_key = "HW3_JOIN_ID"
            if any(f.name.upper() == temp_key for f in arcpy.ListFields(output_fc)):
                raise ValueError(f"Reserved temporary field already exists: {temp_key}")
            arcpy.management.AddField(output_fc, temp_key, "TEXT", field_length=255)
            arcpy.management.AddField(temporary_table, temp_key, "TEXT", field_length=255)
            expression = (f"(str(!{join_field}!).strip().lstrip('0') or '0') "
                          f"if str(!{join_field}!).strip().isdigit() "
                          f"else str(!{join_field}!).strip()")
            arcpy.management.CalculateField(output_fc, temp_key, expression, "PYTHON3")
            arcpy.management.CalculateField(temporary_table, temp_key, expression, "PYTHON3")
            arcpy.management.JoinField(output_fc, temp_key, temporary_table,
                                       temp_key, [value_field])
            arcpy.management.DeleteField(output_fc, temp_key)

        print(f"Prepared feature class: {output_fc}")
        return output_fc
    except Exception as exc:
        if created_output and output_fc and arcpy.Exists(output_fc):
            arcpy.management.Delete(output_fc)
        print(f"prepare_data failed: {exc}")
        raise
    finally:
        if temporary_table and arcpy.Exists(temporary_table):
            arcpy.management.Delete(temporary_table)


# Problem 2 (40 points)
def estimatePopulationInZone(source_polygons, population_field, study_zone,
                             output_fc):
    """Similar to the week 10 and 11 notebook exercises, you overlay a buffer with source_polygons 
    such as census units, and calculate the proportion of a source polygon that overlaps 
    with the buffer (the study zone) using areal interpolation. 
    Combine the study zone, intersect, calculate area-weighted population,
    and total the estimates. Save SRC_KM2, PART_KM2, and EST_POP; return the total.
    Inputs must share a projected CRS in meters. 
    """
    """Intersect source polygons with the dissolved zone and estimate population."""
    temporary = []
    created_output = False
    output_path = str(Path(output_fc))
    try:
        source = str(Path(source_polygons))
        zone = str(Path(study_zone))
        for path, label in ((source, "Source polygons"), (zone, "Study zone")):
            if not arcpy.Exists(path):
                raise FileNotFoundError(f"{label} not found: {path}")
            desc = arcpy.Describe(path)
            if desc.shapeType != "Polygon":
                raise ValueError(f"{label} must have polygon geometry.")
            sr = desc.spatialReference
            if sr is None or sr.type != "Projected" or abs(sr.metersPerUnit - 1) > 1e-8:
                raise ValueError(f"{label} must have a known projected CRS in meters.")

        sr_source = arcpy.Describe(source).spatialReference
        sr_zone = arcpy.Describe(zone).spatialReference
        if sr_source.exportToString() != sr_zone.exportToString():
            raise ValueError("Source polygons and study zone must share the same CRS.")

        fields = {field.name.lower(): field for field in arcpy.ListFields(source)}
        key = population_field.lower()
        if key not in fields:
            raise ValueError(f"Missing population field: {population_field}")
        if fields[key].type not in ("SmallInteger", "Integer", "BigInteger", "Single", "Double"):
            raise ValueError("Population field must be numeric.")
        for field in ("SRC_KM2", "PART_KM2", "EST_POP"):
            if field.lower() in fields:
                raise ValueError(f"Reserved output field already exists: {field}")

        gdb = str(Path(output_path).parent)
        if not arcpy.Exists(gdb) or not gdb.lower().endswith(".gdb"):
            raise ValueError("Output must be inside an existing file geodatabase.")
        if arcpy.Exists(output_path):
            raise FileExistsError(f"Output already exists: {output_path}")
        if not arcpy.ValidateTableName(Path(output_path).name, gdb) == Path(output_path).name:
            raise ValueError("Invalid output feature class name.")

        with arcpy.da.SearchCursor(source, [population_field, "SHAPE@AREA"]) as rows:
            for population, area in rows:
                if population is None or not 0 <= population or area <= 0:
                    raise ValueError("Population must be nonnegative and source areas positive.")

        # Copy the source to memory so its full area survives the intersection.
        source_copy = arcpy.CreateUniqueName("hw3_source", "memory")
        temporary.append(source_copy)
        arcpy.management.CopyFeatures(source, source_copy)
        arcpy.management.AddField(source_copy, "SRC_KM2", "DOUBLE")
        arcpy.management.CalculateGeometryAttributes(
            source_copy, [["SRC_KM2", "AREA"]],
            area_unit="SQUARE_KILOMETERS"
        )

        # Dissolving prevents overlapping study-zone polygons being counted twice.
        zone_union = arcpy.CreateUniqueName("hw3_zone", "memory")
        temporary.append(zone_union)
        arcpy.management.Dissolve(zone, zone_union)

        arcpy.analysis.Intersect([source_copy, zone_union], output_path, "ALL", output_type="INPUT")
        created_output = True
        arcpy.management.AddField(output_path, "PART_KM2", "DOUBLE")
        arcpy.management.AddField(output_path, "EST_POP", "DOUBLE")
        # An empty intersection is a valid result. Keep the empty feature
        # class and required fields, but skip geometry tools on zero rows.
        if int(arcpy.management.GetCount(output_path)[0]) == 0:
            print("Estimated population: 0.0")
            return 0.0
        arcpy.management.CalculateGeometryAttributes(
            output_path, [["PART_KM2", "AREA"]],
            area_unit="SQUARE_KILOMETERS"
        )
        arcpy.management.CalculateField(
            output_path, "EST_POP",
            f"!{population_field}! * (!PART_KM2! / !SRC_KM2!)",
            "PYTHON3"
        )
        stats = arcpy.CreateUniqueName("hw3_pop_stats", "memory")
        temporary.append(stats)
        arcpy.analysis.Statistics(output_path, stats, [["EST_POP", "SUM"]])
        total = 0.0
        with arcpy.da.SearchCursor(stats, ["SUM_EST_POP"]) as rows:
            for (value,) in rows:
                total = float(value or 0)
        print(f"Estimated population: {total}")
        return total
    except Exception as exc:
        if created_output and arcpy.Exists(output_path):
            arcpy.management.Delete(output_path)
        print(f"estimatePopulationInZone failed: {exc}")
        raise
    finally:
        for path in reversed(temporary):
            if arcpy.Exists(path):
                arcpy.management.Delete(path)


# Problem 3 (40 points)
def summarizeLineLength(lines, polygons, polygon_id_field, output_fc):
    """Intersect lines and polygons, sum line kilometers by polygon ID,
    and save every original polygon with LINE_KM (zero for no lines).
    """
    line_path = str(Path(lines))
    polygon_path = str(Path(polygons))
    output_path = str(Path(output_fc))
    temp = []
    created_output = False

    try:
        # Check inputs and coordinate systems; do not reproject here.
        for path, label, expected_shape in (
            (line_path, "Lines", "Polyline"),
            (polygon_path, "Polygons", "Polygon"),
        ):
            if not arcpy.Exists(path):
                raise FileNotFoundError(f"{label} not found: {path}")
            description = arcpy.Describe(path)
            if description.shapeType != expected_shape:
                raise ValueError(f"{label} must have {expected_shape} geometry")
            sr = description.spatialReference
            if sr is None or sr.type != "Projected" or abs(sr.metersPerUnit - 1.0) > 1e-8:
                raise ValueError(f"{label} must have a known projected CRS in meters")

        if (arcpy.Describe(line_path).spatialReference.exportToString() !=
                arcpy.Describe(polygon_path).spatialReference.exportToString()):
            raise ValueError("Lines and polygons must use the same projected CRS")

        polygon_fields = {f.name.lower(): f for f in arcpy.ListFields(polygon_path)}
        key = polygon_id_field.lower()
        if key not in polygon_fields:
            raise ValueError(f"Polygon ID field not found: {polygon_id_field}")
        id_field = polygon_fields[key]
        if id_field.type not in ("String", "SmallInteger", "Integer", "BigInteger"):
            raise ValueError("Polygon ID must be a text or integer field")
        if "line_km" in polygon_fields:
            raise ValueError("Input polygons already have a LINE_KM field")
        if "seg_km" in polygon_fields or "sum_seg_km" in polygon_fields:
            raise ValueError("Input polygons contain a reserved temporary field name")

        ids = set()
        with arcpy.da.SearchCursor(polygon_path, [id_field.name, "SHAPE@AREA"]) as rows:
            for value, area in rows:
                if value is None or (isinstance(value, str) and not value.strip()):
                    raise ValueError("Polygon IDs cannot be null or blank")
                if value in ids:
                    raise ValueError(f"Duplicate polygon ID: {value}")
                ids.add(value)
                if area is None or area <= 0:
                    raise ValueError("Polygon geometries must have positive area")

        gdb = str(Path(output_path).parent)
        if not arcpy.Exists(gdb) or not gdb.lower().endswith(".gdb"):
            raise ValueError("Output must be in an existing file geodatabase")
        if arcpy.Exists(output_path):
            raise FileExistsError(f"Output already exists: {output_path}")
        if arcpy.ValidateTableName(Path(output_path).name, gdb) != Path(output_path).name:
            raise ValueError("Invalid output feature class name")

        # Copy polygons first, so those with no roads are retained.
        arcpy.management.CopyFeatures(polygon_path, output_path)
        created_output = True
        arcpy.management.AddField(output_path, "LINE_KM", "DOUBLE")
        arcpy.management.CalculateField(output_path, "LINE_KM", "0.0", "PYTHON3")

        # Intersect as LINE: point-only touches contribute no length.
        overlap = arcpy.CreateUniqueName("hw3_line_intersect", "memory")
        temp.append(overlap)
        arcpy.analysis.Intersect([line_path, polygon_path], overlap,
                                 "ALL", output_type="LINE")
        if int(arcpy.management.GetCount(overlap)[0]) == 0:
            print(f"No overlapping line segments. Output: {output_path}")
            return output_path

        arcpy.management.AddField(overlap, "SEG_KM", "DOUBLE")
        arcpy.management.CalculateGeometryAttributes(
            overlap, [["SEG_KM", "LENGTH"]], length_unit="KILOMETERS"
        )
        stats = arcpy.CreateUniqueName("hw3_line_stats", "memory")
        temp.append(stats)
        arcpy.analysis.Statistics(overlap, stats, [["SEG_KM", "SUM"]], id_field.name)

        # Join totals and replace zero only for polygons that have line pieces.
        arcpy.management.JoinField(output_path, id_field.name,
                                   stats, id_field.name, ["SUM_SEG_KM"])
        arcpy.management.CalculateField(
            output_path, "LINE_KM",
            "0.0 if !SUM_SEG_KM! is None else !SUM_SEG_KM!", "PYTHON3"
        )
        arcpy.management.DeleteField(output_path, ["SUM_SEG_KM"])
        print(f"Road lengths saved: {output_path}")
        return output_path

    except Exception as exc:
        if created_output and arcpy.Exists(output_path):
            arcpy.management.Delete(output_path)
        print(f"summarizeLineLength failed: {exc}")
        raise
    finally:
        for item in reversed(temp):
            if arcpy.Exists(item):
                arcpy.management.Delete(item)


######################################################################
# MAKE NO CHANGES BEYOND THIS POINT.
######################################################################
if __name__ == "__main__" and hawkid()[1] == "hawkid":
    print("### Error: YOU MUST provide your HawkID in the hawkid() function.")
