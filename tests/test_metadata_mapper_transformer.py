"""Tests for ValueTransformer and apply_mappings."""

import pytest
from pydantic import ValidationError

from nmdc_metadata_suggestor_ai_tool.metadata_mapper.apply import (
    apply_mappings,
    build_conversion_previews,
)
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transformer import (
    TransformError,
    ValueTransformer,
)
from nmdc_metadata_suggestor_ai_tool.models.metadata_mapper_output import (
    ColumnMapping,
    MetadataMapperOutput,
    SourceFile,
    ValueConversion,
)

transformer = ValueTransformer()


# ------------------------------------------------------------------
# ValueConversion model validator
# ------------------------------------------------------------------


class TestValueConversionValidator:
    def test_expression_required_for_date_format(self):
        with pytest.raises(ValidationError, match="expression is required"):
            ValueConversion(type="date_format", description="", expression=None)

    def test_expression_required_for_unit(self):
        with pytest.raises(ValidationError, match="expression is required"):
            ValueConversion(type="unit", description="", expression=None)

    def test_expression_required_for_custom(self):
        with pytest.raises(ValidationError, match="expression is required"):
            ValueConversion(type="custom", description="", expression=None)

    def test_none_type_allows_null_expression(self):
        conv = ValueConversion(type="none", description="", expression=None)
        assert conv.expression is None


# ------------------------------------------------------------------
# ValueTransformer unit tests
# ------------------------------------------------------------------


class TestDateFormat:
    def test_parses_mdy(self):
        conv = ValueConversion(type="date_format", description="", expression="%m/%d/%Y")
        assert transformer.transform("01/15/2023", conv) == "2023-01-15"

    def test_parses_dmy(self):
        conv = ValueConversion(type="date_format", description="", expression="%d-%b-%Y")
        assert transformer.transform("15-Jan-2023", conv) == "2023-01-15"

    def test_invalid_value_raises(self):
        conv = ValueConversion(type="date_format", description="", expression="%m/%d/%Y")
        with pytest.raises(TransformError):
            transformer.transform("not-a-date", conv)


class TestUnit:
    def test_feet_to_meters(self):
        conv = ValueConversion(type="unit", description="", expression="0.3048")
        result = transformer.transform("100", conv)
        assert float(result) == pytest.approx(30.48)

    def test_value_with_label(self):
        conv = ValueConversion(type="unit", description="", expression="0.3048")
        result = transformer.transform("100 ft", conv)
        assert float(result) == pytest.approx(30.48)

    def test_non_numeric_raises(self):
        conv = ValueConversion(type="unit", description="", expression="0.3048")
        with pytest.raises(TransformError):
            transformer.transform("unknown", conv)


class TestSplit:
    def test_comma_split(self):
        conv = ValueConversion(type="split", description="", expression=",")
        assert transformer.transform("a, b, c", conv) == "a; b; c"

    def test_pipe_split(self):
        conv = ValueConversion(type="split", description="", expression="|")
        assert transformer.transform("x|y|z", conv) == "x; y; z"


class TestNone:
    def test_passthrough(self):
        conv = ValueConversion(type="none", description="", expression=None)
        assert transformer.transform("unchanged", conv) == "unchanged"

    def test_no_expression_passthrough(self):
        # type='none' with no expression is the only valid null-expression case.
        conv = ValueConversion(type="none", description="", expression=None)
        assert transformer.transform("unchanged", conv) == "unchanged"


class TestCustom:
    def test_simple_expression(self):
        conv = ValueConversion(type="custom", description="", expression="value.upper()")
        assert transformer.transform("hello", conv) == "HELLO"

    def test_date_reformat(self):
        conv = ValueConversion(
            type="custom",
            description="",
            expression="'-'.join(value.split('/')[::-1])",
        )
        assert transformer.transform("15/01/2023", conv) == "2023-01-15"

    def test_numeric_result_coerced_to_str(self):
        conv = ValueConversion(type="custom", description="", expression="int(value) * 2")
        assert transformer.transform("5", conv) == "10"

    def test_timeout_raises(self, monkeypatch):
        import nmdc_metadata_suggestor_ai_tool.metadata_mapper.transformer as t_module

        monkeypatch.setattr(t_module, "_EXEC_TIMEOUT_S", 1)
        # sorted(range(10**9)) allocates ~8 GB and runs for many seconds — times out.
        conv = ValueConversion(
            type="custom",
            description="",
            expression="str(sorted(range(10 ** 9))[0])",
        )
        with pytest.raises(TransformError, match="timed out"):
            transformer.transform("x", conv)

    def test_dangerous_import_blocked(self):
        conv = ValueConversion(
            type="custom",
            description="",
            expression="__import__('os').getcwd()",
        )
        with pytest.raises(TransformError):
            transformer.transform("x", conv)


# ------------------------------------------------------------------
# apply_mappings integration tests
# ------------------------------------------------------------------


def _make_output(mappings: list[ColumnMapping]) -> MetadataMapperOutput:
    source_file = SourceFile(file_id="f1", display_name="test.csv")
    return MetadataMapperOutput(
        source_files=[source_file],
        high_confidence=mappings,
    )


class TestApplyMappings:
    def test_simple_mapping_no_conversion(self):
        mapping = ColumnMapping(
            source_column="sample_name",
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["samp_name"],
            confidence="high",
            reason="",
            conversion=None,
        )
        rows = [{"sample_name": "SOIL_001", "other": "val"}]
        result = apply_mappings(_make_output([mapping]), rows)
        # Mapped column is renamed to its slot key.
        assert result[0]["samp_name"] == "SOIL_001"
        assert "sample_name" not in result[0]
        # Unmapped columns are preserved.
        assert result[0]["other"] == "val"

    def test_unit_conversion_applied(self):
        conv = ValueConversion(type="unit", description="ft to m", expression="0.3048")
        mapping = ColumnMapping(
            source_column="depth_ft",
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["depth"],
            confidence="high",
            reason="",
            conversion=conv,
        )
        rows = [{"depth_ft": "100"}]
        result = apply_mappings(_make_output([mapping]), rows)
        assert float(result[0]["depth"]) == pytest.approx(30.48)

    def test_missing_source_column_skipped(self):
        mapping = ColumnMapping(
            source_column="nonexistent",
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["depth"],
            confidence="high",
            reason="",
            conversion=None,
        )
        rows = [{"other": "val"}]
        result = apply_mappings(_make_output([mapping]), rows)
        assert "depth" not in result[0]
        assert "_transform_errors" not in result[0]

    def test_transform_error_captured_not_raised(self):
        conv = ValueConversion(type="date_format", description="", expression="%m/%d/%Y")
        mapping = ColumnMapping(
            source_column="date_col",
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["collection_date"],
            confidence="high",
            reason="",
            conversion=conv,
        )
        rows = [{"date_col": "not-a-date"}]
        result = apply_mappings(_make_output([mapping]), rows)
        assert result[0]["_transform_errors"]
        assert result[0]["collection_date"] == "not-a-date"

    def test_source_file_id_filter(self):
        mapping_f1 = ColumnMapping(
            source_column="col_a",
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["slot_a"],
            confidence="high",
            reason="",
        )
        mapping_f2 = ColumnMapping(
            source_column="col_b",
            source_file_id="f2",
            mixs_extension="Soil",
            nmdc_candidate_slots=["slot_b"],
            confidence="high",
            reason="",
        )
        output = MetadataMapperOutput(
            source_files=[
                SourceFile(file_id="f1", display_name="a.csv"),
                SourceFile(file_id="f2", display_name="b.csv"),
            ],
            high_confidence=[mapping_f1, mapping_f2],
        )
        rows = [{"col_a": "val_a", "col_b": "val_b"}]
        result = apply_mappings(output, rows, source_file_id="f1")
        assert "slot_a" in result[0]
        assert "slot_b" not in result[0]

    def test_cant_place_mappings_ignored(self):
        cant = ColumnMapping(
            source_column="mystery_col",
            source_file_id="f1",
            mixs_extension=None,
            nmdc_candidate_slots=[],
            confidence="cant_place",
            reason="",
        )
        output = MetadataMapperOutput(
            source_files=[SourceFile(file_id="f1", display_name="test.csv")],
            cant_place=[cant],
        )
        rows = [{"mystery_col": "val"}]
        result = apply_mappings(output, rows)
        assert "_transform_errors" not in result[0]


class TestBuildConversionPreviews:
    def test_populates_preview_from_real_rows(self):
        conv = ValueConversion(type="unit", description="ft to m", expression="0.3048")
        mapping = ColumnMapping(
            source_column="depth_ft",
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["depth"],
            confidence="high",
            reason="",
            conversion=conv,
        )
        output = _make_output([mapping])
        rows = [{"depth_ft": "100"}, {"depth_ft": "50"}, {"depth_ft": "200"}]
        build_conversion_previews(output, rows, n=3)

        assert len(conv.preview) == 3
        assert conv.preview[0] == {"input": "100", "output": "30.48"}

    def test_respects_n_limit(self):
        conv = ValueConversion(type="unit", description="", expression="0.3048")
        mapping = ColumnMapping(
            source_column="depth_ft",
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["depth"],
            confidence="high",
            reason="",
            conversion=conv,
        )
        output = _make_output([mapping])
        rows = [{"depth_ft": str(i)} for i in range(10)]
        build_conversion_previews(output, rows, n=2)

        assert len(conv.preview) == 2

    def test_skips_empty_values(self):
        conv = ValueConversion(type="unit", description="", expression="0.3048")
        mapping = ColumnMapping(
            source_column="depth_ft",
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["depth"],
            confidence="high",
            reason="",
            conversion=conv,
        )
        output = _make_output([mapping])
        rows = [{"depth_ft": ""}, {"depth_ft": "100"}]
        build_conversion_previews(output, rows, n=3)

        assert len(conv.preview) == 1
        assert conv.preview[0]["input"] == "100"

    def test_records_transform_errors_in_preview(self):
        conv = ValueConversion(type="date_format", description="", expression="%m/%d/%Y")
        mapping = ColumnMapping(
            source_column="date_col",
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["collection_date"],
            confidence="high",
            reason="",
            conversion=conv,
        )
        output = _make_output([mapping])
        rows = [{"date_col": "not-a-date"}]
        build_conversion_previews(output, rows)

        assert conv.preview[0]["output"] is None
        assert "error" in conv.preview[0]

    def test_none_type_skipped(self):
        conv = ValueConversion(type="none", description="", expression=None)
        mapping = ColumnMapping(
            source_column="col",
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["slot"],
            confidence="high",
            reason="",
            conversion=conv,
        )
        output = _make_output([mapping])
        build_conversion_previews(output, [{"col": "val"}])

        assert conv.preview == []


# ------------------------------------------------------------------
# combine_columns — model validator
# ------------------------------------------------------------------


class TestCombineColumnsValidator:
    def test_combine_requires_custom_type(self):
        with pytest.raises(ValidationError, match="requires conversion.type='custom'"):
            ColumnMapping(
                source_column="lat",
                combine_columns=["lon"],
                source_file_id="f1",
                mixs_extension="Soil",
                nmdc_candidate_slots=["lat_lon"],
                confidence="high",
                reason="",
                conversion=ValueConversion(type="unit", description="", expression="1.0"),
            )

    def test_combine_with_custom_is_valid(self):
        mapping = ColumnMapping(
            source_column="lat",
            combine_columns=["lon"],
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["lat_lon"],
            confidence="high",
            reason="",
            conversion=ValueConversion(
                type="custom",
                description="combine lat lon",
                expression="f\"{values['lat']} {values['lon']}\"",
            ),
        )
        assert mapping.combine_columns == ["lon"]


# ------------------------------------------------------------------
# ValueTransformer.transform_combined
# ------------------------------------------------------------------


class TestTransformCombined:
    def test_lat_lon_combine(self):
        conv = ValueConversion(
            type="custom",
            description="",
            expression="f\"{values['lat']} {values['lon']}\"",
        )
        result = transformer.transform_combined({"lat": "45.2", "lon": "-122.3"}, conv)
        assert result == "45.2 -122.3"

    def test_full_name_combine(self):
        conv = ValueConversion(
            type="custom",
            description="",
            expression="f\"{values['first']} {values['last']}\"",
        )
        result = transformer.transform_combined({"first": "Jane", "last": "Smith"}, conv)
        assert result == "Jane Smith"

    def test_numeric_result_coerced(self):
        conv = ValueConversion(
            type="custom",
            description="",
            expression="str(float(values['a']) + float(values['b']))",
        )
        result = transformer.transform_combined({"a": "1.5", "b": "2.5"}, conv)
        assert result == "4.0"

    def test_missing_key_raises(self):
        conv = ValueConversion(
            type="custom",
            description="",
            expression="f\"{values['lat']} {values['lon']}\"",
        )
        with pytest.raises(TransformError):
            transformer.transform_combined({"lat": "45.2"}, conv)


# ------------------------------------------------------------------
# apply_mappings — combine_columns integration
# ------------------------------------------------------------------


class TestApplyMappingsCombine:
    def _make_combine_mapping(self, primary: str, others: list[str], slot: str) -> ColumnMapping:
        return ColumnMapping(
            source_column=primary,
            combine_columns=others,
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=[slot],
            confidence="high",
            reason="",
            conversion=ValueConversion(
                type="custom",
                description="combine",
                expression="f\"{values['" + primary + "']} {values['" + others[0] + "']}\"",
            ),
        )

    def test_combined_columns_produce_slot(self):
        mapping = self._make_combine_mapping("lat", ["lon"], "lat_lon")
        output = _make_output([mapping])
        rows = [{"lat": "45.2", "lon": "-122.3", "other": "x"}]
        result = apply_mappings(output, rows)
        assert result[0]["lat_lon"] == "45.2 -122.3"

    def test_combined_source_columns_removed(self):
        mapping = self._make_combine_mapping("lat", ["lon"], "lat_lon")
        output = _make_output([mapping])
        rows = [{"lat": "45.2", "lon": "-122.3"}]
        result = apply_mappings(output, rows)
        assert "lat" not in result[0]
        assert "lon" not in result[0]

    def test_unmapped_column_preserved(self):
        mapping = self._make_combine_mapping("lat", ["lon"], "lat_lon")
        output = _make_output([mapping])
        rows = [{"lat": "45.2", "lon": "-122.3", "other": "keep_me"}]
        result = apply_mappings(output, rows)
        assert result[0]["other"] == "keep_me"

    def test_missing_combine_column_records_error(self):
        mapping = self._make_combine_mapping("lat", ["lon"], "lat_lon")
        output = _make_output([mapping])
        rows = [{"lat": "45.2"}]  # lon missing
        result = apply_mappings(output, rows)
        assert "_transform_errors" in result[0]
        assert "lat_lon" not in result[0]


# ------------------------------------------------------------------
# build_conversion_previews — combine path
# ------------------------------------------------------------------


class TestBuildConversionPreviewsCombine:
    def test_combine_previews_from_real_rows(self):
        conv = ValueConversion(
            type="custom",
            description="",
            expression="f\"{values['lat']} {values['lon']}\"",
        )
        mapping = ColumnMapping(
            source_column="lat",
            combine_columns=["lon"],
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["lat_lon"],
            confidence="high",
            reason="",
            conversion=conv,
        )
        output = _make_output([mapping])
        rows = [{"lat": "45.2", "lon": "-122.3"}, {"lat": "34.0", "lon": "-118.2"}]
        build_conversion_previews(output, rows, n=2)

        assert len(conv.preview) == 2
        assert conv.preview[0]["output"] == "45.2 -122.3"
        assert isinstance(conv.preview[0]["input"], dict)

    def test_combine_previews_skip_rows_with_missing_columns(self):
        conv = ValueConversion(
            type="custom",
            description="",
            expression="f\"{values['lat']} {values['lon']}\"",
        )
        mapping = ColumnMapping(
            source_column="lat",
            combine_columns=["lon"],
            source_file_id="f1",
            mixs_extension="Soil",
            nmdc_candidate_slots=["lat_lon"],
            confidence="high",
            reason="",
            conversion=conv,
        )
        output = _make_output([mapping])
        rows = [{"lat": "45.2"}, {"lat": "34.0", "lon": "-118.2"}]
        build_conversion_previews(output, rows, n=3)

        assert len(conv.preview) == 1
        assert conv.preview[0]["output"] == "34.0 -118.2"
