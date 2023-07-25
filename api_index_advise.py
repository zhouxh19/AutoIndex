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
import logging
from contextlib import contextmanager
import psycopg2

from . import index_advisor_workload, process_bar
from .executors.driver_executor import DriverExecutor


class Executor(DriverExecutor):
    def __init__(self, dbname=None, user=None, password=None, host=None, port=None, schema=None):
        super().__init__(dbname, user, password, host, port, schema)
        self.conn = None
        self.cur = None

    def set_connection(self, connection):
        self.conn = connection
        self.cur = self.conn.cursor()

    def set_schemas(self, schemas):
        self.schema = ','.join(schemas)

    @contextmanager
    def session(self):
        yield


def api_index_advise(sql_pairs, connection=None, dsn=None,
                     schemas=("public",), improved_rate=0.5,
                     improved_cost=None, max_index_num=None,
                     max_index_columns=3, min_n_distinct=20,
                     **connection_kwargs):
    search_path = None
    if min_n_distinct <= 0:
        raise ValueError('min_n_distinct is an invalid positive int value')
    process_bar.print = lambda *args, **kwargs: None
    # nly single thread can be used
    index_advisor_workload.get_workload_costs = index_advisor_workload.get_plan_cost
    templates = dict()
    executor = Executor()
    executor.set_schemas(schemas)
    if connection:
        cursor = connection.cursor()
        cursor.execute('show search_path;')
        search_path = cursor.fetchone()[0]
        connection.commit()
    elif dsn:
        connection = psycopg2.connect(dsn=dsn)
    else:
        connection = psycopg2.connect(**connection_kwargs)
    executor.set_connection(connection)

    for sql, count in sql_pairs.items():
        templates[sql] = {'samples': [sql],
                          'cnt': count}
    if max_index_num:
        index_advisor_workload.MAX_INDEX_NUM = max_index_num
    if improved_cost:
        index_advisor_workload.MAX_BENEFIT_THRESHOLD = improved_cost
    detail_info, advised_indexes, _redundant_indexes = index_advisor_workload.index_advisor_workload(
        {'historyIndexes': {}}, executor, templates,
        multi_iter_mode=True, show_detail=True,
        n_distinct=0.02, reltuples=10, show_benefits=True,
        use_all_columns=True, improved_rate=improved_rate,
        max_index_columns=max_index_columns, max_n_distinct=1 / min_n_distinct,
    )

    redundant_indexes = []
    for index in _redundant_indexes:
        if index.get_is_unique() or index.is_primary_key():
            continue
        statement = "DROP INDEX %s.%s;(%s)" % (index.get_schema(), index.get_indexname(), index.get_indexdef())
        related_indexes = []
        for _index in index.redundant_objs:
            related_indexes.append(_index.get_indexdef())
        redundant_indexes.append({'redundant_index': statement,
                                  'related_indexes': related_indexes})
    cursor = connection.cursor()
    if search_path:
        cursor.execute(f'set current_schema={search_path}')
        connection.commit()
    else:
        cursor.close()
        connection.close()

    return advised_indexes, redundant_indexes
