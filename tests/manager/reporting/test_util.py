import io
from collections.abc import Generator
from functools import partial
from typing import Any
from unittest.mock import MagicMock, Mock

import _csv
import pytest
from sqlalchemy.engine import Result
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from palace.manager.reporting.util import (
    TabularQueryDefinition,
    row_counter_wrapper,
    write_csv,
)


class TestReportTableProcessors:
    @pytest.mark.parametrize(
        "rows, headings, delimiter, expected",
        [
            pytest.param(
                [["row1", "data1"], ["row2", "data2"]],
                None,
                ",",
                "row1,data1\r\nrow2,data2\r\n",
                id="no_headings",
            ),
            pytest.param(
                [["data1", "data2"], ["data3", "data4"]],
                ["header1", "header2"],
                ",",
                "header1,header2\r\ndata1,data2\r\ndata3,data4\r\n",
                id="with_headings",
            ),
            pytest.param(
                [["row1", "data1"], ["row2", "data2"]],
                ["header1", "header2"],
                "|",
                "header1|header2\r\nrow1|data1\r\nrow2|data2\r\n",
                id="different_delimiter",
            ),
            pytest.param(
                [], ["header1", "header2"], ",", "header1,header2\r\n", id="empty_rows"
            ),
            pytest.param([], None, ",", "", id="empty_rows_no_headings"),
            pytest.param(
                [["data1"]],
                ["header1"],
                ",",
                "header1\r\ndata1\r\n",
                id="single_row_single_column",
            ),
            pytest.param(
                [[1, "data1", 3.14]],
                None,
                ",",
                "1,data1,3.14\r\n",
                id="different_data_types",
            ),
        ],
    )
    def test_write_csv(
        self,
        rows: list[list[Any]],
        headings: list[str] | None,
        delimiter: str,
        expected: str,
    ) -> None:
        output = io.StringIO()

        write_csv(output, rows, headings, delimiter)

        output_str = output.getvalue()
        assert output_str == expected

    @pytest.mark.parametrize(
        "rows, headings, delimiter, expected_error, expected_match",
        [
            pytest.param(
                123,
                ["header1", "header2"],
                ",",
                TypeError,
                "object is not iterable",
                id="rows_not_iterable",
            ),
            pytest.param(
                [1, 2, 3],
                None,
                ",",
                _csv.Error,
                "iterable expected",
                id="rows_not_iterable_of_iterables",
            ),
        ],
    )
    def test_write_csv_error_cases(
        self,
        rows: Any,
        headings: list[str] | None,
        delimiter: str,
        expected_error: type[Exception],
        expected_match: str,
    ) -> None:
        output = io.StringIO()

        with pytest.raises(expected_error, match=expected_match):
            write_csv(output, rows, headings, delimiter)

    @pytest.mark.parametrize(
        "rows, headings, expected",
        [
            pytest.param(
                [["row1", "data1"], ["row2", "data2"]],
                None,
                "row1|data1\r\nrow2|data2\r\n",
                id="no_headings",
            ),
            pytest.param(
                [["data1", "data2"], ["data3", "data4"]],
                ["header1", "header2"],
                "header1|header2\r\ndata1|data2\r\ndata3|data4\r\n",
                id="with_headings",
            ),
        ],
    )
    def test_write_csv_partial(
        self, rows: list[list[Any]], headings: list[str] | None, expected: str
    ):
        output = io.StringIO()
        csv_to_output_w_pipe_sep = partial(write_csv, output, delimiter="|")
        csv_to_output_w_pipe_sep(rows, headings)

        assert output.getvalue() == expected

    @pytest.mark.parametrize(
        "rows, headings, expected_output, expected_count",
        [
            pytest.param(
                [["row1", "data1"], ["row2", "data2"]],
                None,
                "row1,data1\r\nrow2,data2\r\n",
                2,
                id="no_headings",
            ),
            pytest.param(
                [["data1", "data2"], ["data3", "data4"]],
                ["header1", "header2"],
                "header1,header2\r\ndata1,data2\r\ndata3,data4\r\n",
                2,
                id="with_headings",
            ),
            pytest.param(
                [], ["header1", "header2"], "header1,header2\r\n", 0, id="no_rows"
            ),
            pytest.param(
                [["data1"]], ["header1"], "header1\r\ndata1\r\n", 1, id="single_row"
            ),
        ],
    )
    def test_write_csv_row_counter_wrapper(
        self,
        rows: list[list[Any]],
        headings: list[str] | None,
        expected_output: str,
        expected_count: int,
    ):
        output = io.StringIO()
        table_data_processor = partial(write_csv, output)
        counting_table_data_processor = row_counter_wrapper(table_data_processor)

        counted_rows, result = counting_table_data_processor(rows, headings)

        assert counted_rows.count == expected_count
        assert output.getvalue() == expected_output


class TestTabularQueryDefinition:

    @pytest.fixture
    def mock_statement(self) -> MagicMock:
        """Creates a mock SQLAlchemy Select statement."""
        statement = MagicMock(spec=Select)

        # Mock the column objects.
        mock_col_a = Mock()
        mock_col_a.name = "Column A"
        mock_col_b = Mock()
        mock_col_b.name = "Column B"
        mock_columns = [mock_col_a, mock_col_b]
        statement.c = mock_columns

        # Mock the .params() method to return a mock statement to simulates how `bindparam` works.
        mock_parameterized_statement = MagicMock(spec=Select)
        statement.params.return_value = mock_parameterized_statement

        return statement

    @pytest.fixture
    def mock_session(self) -> MagicMock:
        """Creates a mock SQLAlchemy Session."""
        return MagicMock(spec=Session)

    def test_initialization(self, mock_statement: MagicMock):
        """Test that __post_init__ derives headings and properties are stored."""
        report_id = "test-report"
        report_title = "Test Report Title"

        definition = TabularQueryDefinition(
            id=report_id, title=report_title, statement=mock_statement
        )

        assert definition.id == report_id
        assert definition.title == report_title
        assert definition.statement is mock_statement
        assert definition.headings == ["Column A", "Column B"]

    def test_init_with_no_columns(self):
        """Test initialization when the statement has no columns.

        This should not really happen in real life; but, you know...
        """
        report_id = "no-cols-report"
        report_title = "No Columns Report"
        mock_statement_no_cols = MagicMock(spec=Select)
        mock_statement_no_cols.c = []  # Look ma, no columns!
        mock_statement_no_cols.params.return_value = mock_statement_no_cols

        with pytest.raises(ValueError, match="No columns in "):
            TabularQueryDefinition(
                id=report_id, title=report_title, statement=mock_statement_no_cols
            )

    def test_rows_executes_query_and_yields_results(
        self, mock_statement: MagicMock, mock_session: MagicMock
    ):
        """Test that rows() executes the query and yields results as tuples."""
        expected_data = [(1, "Alice"), (2, "Bob")]
        report_id = "test-rows"
        report_title = "Test Rows Report"

        # Configure the mock session's execute method.
        mock_result = MagicMock(spec=Result)
        mock_result.__iter__.return_value = iter(
            expected_data
        )  # Make the result iterable.
        mock_session.execute.return_value = mock_result

        definition = TabularQueryDefinition(
            id=report_id, title=report_title, statement=mock_statement
        )

        # Call the rows method (no query params in this test).
        result_generator = definition.rows(session=mock_session)

        # Verify it returns a generator.
        assert isinstance(result_generator, Generator)

        # Consume the generator and check results.
        results = list(result_generator)

        # Verify the statement.params() was called (even without params).
        mock_statement.params.assert_called_once_with()
        # Verify session.execute was called with the result of statement.params().
        mock_session.execute.assert_called_once_with(mock_statement.params.return_value)
        # Verify the yielded data matches expected data (and are tuples).
        assert results == expected_data
        assert all(isinstance(row, tuple) for row in results)

    def test_rows_with_query_params(
        self, mock_statement: MagicMock, mock_session: MagicMock
    ):
        """Test that rows() passes query_params to statement.params()."""
        expected_data = [(3, "Charlie")]
        report_id = "test-params"
        report_title = "Test Params Report"
        query_params = {"user_id": 123, "status": "active"}

        # Configure mocks.
        mock_result = MagicMock(spec=Result)
        mock_result.__iter__.return_value = iter(expected_data)
        mock_session.execute.return_value = mock_result

        definition = TabularQueryDefinition(
            id=report_id, title=report_title, statement=mock_statement
        )

        # Call rows with parameters.
        results = list(definition.rows(session=mock_session, **query_params))

        # Verify statement.params was called with the keyword arguments.
        mock_statement.params.assert_called_once_with(**query_params)
        # Verify session.execute was called with the result of statement.params().
        mock_session.execute.assert_called_once_with(mock_statement.params.return_value)
        # Verify results.
        assert results == expected_data

    def test_rows_with_empty_result(
        self, mock_statement: MagicMock, mock_session: MagicMock
    ):
        expected_data: list[tuple[Any, ...]] = []
        expected_headings = ["Column A", "Column B"]
        report_id = "test-empty"
        report_title = "Test Empty Report"

        # Configure mocks for empty result.
        mock_result = MagicMock(spec=Result)
        mock_result.__iter__.return_value = iter(expected_data)  # Empty iterator.
        mock_session.execute.return_value = mock_result

        definition = TabularQueryDefinition(
            id=report_id, title=report_title, statement=mock_statement
        )

        results = list(definition.rows(session=mock_session))

        mock_statement.params.assert_called_once_with()
        mock_session.execute.assert_called_once_with(mock_statement.params.return_value)

        # Also, verify that the headings are still derived correctly.
        assert results == expected_data
        assert definition.headings == expected_headings
