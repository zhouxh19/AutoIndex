# Copyright (c) 2022 Huawei Technologies Co.,Ltd.
#
# openGauss is licensed under Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#
#          http://license.coscl.org.cn/MulanPSL2
#
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
# EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
# MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
# See the Mulan PSL v2 for more details.

from dataclasses import dataclass, field
from typing import List
from functools import lru_cache

try:
    from .sql_generator import get_table_info_sql, get_column_info_sql
    from .executors.common import BaseExecutor
    from .utils import IndexItemFactory
except ImportError:
    from sql_generator import get_table_info_sql, get_column_info_sql
    from executors.common import BaseExecutor
    from utils import IndexItemFactory


@lru_cache(maxsize=None)
def get_table_context(origin_table, executor: BaseExecutor):
    reltuples, parttype = None, None
    if '.' in origin_table:
        schemas, table = origin_table.split('.')
    else:
        table = origin_table
        schemas = executor.get_schema()
    for _schema in schemas.split(','):
        table_info_sqls = [get_table_info_sql(table, _schema)]
        column_info_sqls = [get_column_info_sql(table, _schema)]
        for _tuple in executor.execute_sqls(table_info_sqls):
            if len(_tuple) == 2:
                reltuples, parttype = _tuple
                reltuples = int(float(reltuples))
        if not reltuples:
            continue
        is_partitioned_table = True if parttype == 'p' else False
        columns = []
        n_distincts = []
        for _tuple in executor.execute_sqls(column_info_sqls):
            if len(_tuple) != 2:
                continue
            n_distinct, column = _tuple
            if column not in columns:
                columns.append(column)
                n_distincts.append(float(n_distinct))
        table_context = TableContext(_schema, table, int(reltuples), columns, n_distincts, is_partitioned_table)
        return table_context


@dataclass(eq=False)
class TableContext:
    schema: str
    table: str
    reltuples: int
    columns: List = field(default_factory=lambda: [])
    n_distincts: List = field(default_factory=lambda: [])
    is_partitioned_table: bool = field(default=False)

    @lru_cache(maxsize=None)
    def has_column(self, column):
        is_same_table = True
        if '.' in column:
            if column.split('.')[0].upper() != self.table.split('.')[-1].upper():
                is_same_table = False
            column = column.split('.')[1].lower()
        return is_same_table and column in self.columns

    @lru_cache(maxsize=None)
    def get_n_distinct(self, column):
        column = column.split('.')[-1].lower()
        idx = self.columns.index(column)
        n_distinct = self.n_distincts[idx]
        if float(n_distinct) == float(0):
            return 1
        return 1 / (-n_distinct * self.reltuples) if n_distinct < 0 else 1 / n_distinct
