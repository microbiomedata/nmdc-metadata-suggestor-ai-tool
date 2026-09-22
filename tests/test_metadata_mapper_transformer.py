"""Tests for ValueTransformer and apply_mappings."""

import pytest
from pydantic import ValidationError

from nmdc_metadata_suggestor_ai_tool.metadata_mapper.apply import apply_mappings, build_conversion_previews
from nmdc_metadata_suggestor_ai_tool.metadata_mapper.transformer import TransformError, ValueTransformer
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
        conv = ValueConversion(type="custom", description="", expression="value.upper()", requires_approval=True)
        assert transformer.transform("hello", conv) == "HELLO"

    def test_date_reformat(self):
        conv = ValueConversion(
            type="custom",
            description="",
            expression="'-'.join(value.split('/')[::-1])",
            requires_approval=True,
        )
        assert transformer.transform("15/01/2023", conv) == "2023-01-15"

    def test_numeric_result_coerced_to_str(self):
        conv = ValueConversion(type="custom", description="", expression="int(value) * 2", requires_approval=True)
        assert transformer.transform("5", conv) == "10"

    def test_timeout_raises(self, monkeypatch):
        import nmdc_metadata_suggestor_ai_tool.metadata_mapper.transformer as t_module

        monkeypatch.setattr(t_module, "_EXEC_TIMEOUT_S", 1)
        # sorted(range(10**9)) allocates ~8 GB and runs for many seconds — times out.
        conv = ValueConversion(
            type="custom",
            description="",
            expression="str(sorted(range(10 ** 9))[0])",
            requires_approval=True,
        )
        with pytest.raises(TransformError, match="timed out"):
            transformer.transform("x", conv)

    def test_dangerous_import_blocked(self):
        conv = ValueConversion(
            type="custom",
            description="",
            expression="__import__('os').getcwd()",
            requires_approval=True,
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
            source_files=[SourceFile(file_id="f1", display_name="a.csv"), SourceFile(file_id="f2", display_name="b.csv")],
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
