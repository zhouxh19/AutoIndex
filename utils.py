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

import re
from collections import defaultdict
from enum import Enum
from functools import lru_cache
from typing import List, Tuple, Sequence, Any
from contextlib import contextmanager

import sqlparse
from sqlparse.tokens import Name
from sqlparse.sql import Function, Parenthesis, IdentifierList

COLUMN_DELIMITER = ', '
QUERY_PLAN_SUFFIX = 'QUERY PLAN'
EXPLAIN_SUFFIX = 'EXPLAIN'
ERROR_KEYWORD = 'ERROR'
PREPARE_KEYWORD = 'PREPARE'


class QueryType(Enum):
    INEFFECTIVE = 0
    POSITIVE = 1
    NEGATIVE = 2


class IndexType(Enum):
    ADVISED = 1
    REDUNDANT = 2
    INVALID = 3


def replace_function_comma(statement):
    """Replace the ? in function to the corresponding value to ensure that prepare execution can be executed properly"""
    function_value = {'count': '1', 'decode': "'1'"}
    new_statement = ''
    for token in get_tokens(statement):
        value = token.value
        if token.ttype is Name.Placeholder and token.value == '?':
            function_token = None
            if isinstance(token.parent, Parenthesis) and isinstance(token.parent.parent, Function):
                function_token = token.parent.parent
            elif isinstance(token.parent, IdentifierList) \
                    and isinstance(token.parent.parent, Parenthesis) \
                    and isinstance(token.parent.parent.parent, Function):
                function_token = token.parent.parent.parent
            if function_token:
                replaced_value = function_value.get(function_token.get_name().lower(), None)
                value = replaced_value if replaced_value else value
        new_statement += value
    return new_statement


class UniqueList(list):

    def append(self, item: Any) -> None:
        if item not in self:
            super().append(item)

    def extend(self, items: Sequence[Any]) -> None:
        for item in items:
            self.append(item)


class ExistingIndex:

    def __init__(self, schema, table, indexname, columns, indexdef):
        self.__schema = schema
        self.__table = table
        self.__indexname = indexname
        self.__columns = columns
        self.__indexdef = indexdef
        self.__primary_key = False
        self.__is_unique = False
        self.__index_type = ''
        self.redundant_objs = []

    def set_is_unique(self):
        self.__is_unique = True

    def get_is_unique(self):
        return self.__is_unique

    def set_index_type(self, index_type):
        self.__index_type = index_type

    def get_index_type(self):
        return self.__index_type

    def get_table(self):
        return self.__table

    def get_schema(self):
        return self.__schema

    def get_indexname(self):
        return self.__indexname

    def get_columns(self):
        return self.__columns

    def get_indexdef(self):
        return self.__indexdef

    def is_primary_key(self):
        return self.__primary_key

    def set_is_primary_key(self, is_primary_key: bool):
        self.__primary_key = is_primary_key

    def get_schema_table(self):
        return self.__schema + '.' + self.__table

    def __str__(self):
        return f'{self.__schema}, {self.__table}, {self.__indexname}, {self.__columns}, {self.__indexdef})'

    def __repr__(self):
        return self.__str__()


class AdvisedIndex:
    def __init__(self, tbl, cols, index_type=None):
        self.__table = tbl
        self.__columns = cols
        self.benefit = 0
        self.__storage = 0
        self.__index_type = index_type
        self.association_indexes = defaultdict(list)
        self.__positive_queries = []
        self.__source_index = None

    def set_source_index(self, source_index: ExistingIndex):
        self.__source_index = source_index

    def get_source_index(self):
        return self.__source_index

    def append_positive_query(self, query):
        self.__positive_queries.append(query)

    def get_positive_queries(self):
        return self.__positive_queries

    def set_storage(self, storage):
        self.__storage = storage

    def get_storage(self):
        return self.__storage

    def get_table(self):
        return self.__table

    def get_schema(self):
        return self.__table.split('.')[0]

    def get_columns(self):
        return self.__columns

    def get_columns_num(self):
        return len(self.get_columns().split(COLUMN_DELIMITER))

    def get_index_type(self):
        return self.__index_type

    def get_index_statement(self):
        table_name = self.get_table().split('.')[-1]
        index_name = 'idx_%s_%s%s' % (table_name, (self.get_index_type() + '_' if self.get_index_type() else ''),
                                      '_'.join(self.get_columns().split(COLUMN_DELIMITER))
                                      )
        statement = 'CREATE INDEX %s ON %s%s%s;' % (index_name, self.get_table(),
                                                    '(' + self.get_columns() + ')',
                                                    (' ' + self.get_index_type() if self.get_index_type() else '')
                                                    )
        return statement

    def set_association_indexes(self, association_indexes_name, association_benefit):
        self.association_indexes[association_indexes_name].append(association_benefit)

    def match_index_name(self, index_name):
        schema = self.get_schema()
        if schema == 'public':
            return index_name.endswith(f'btree_{self.get_index_type() + "_" if self.get_index_type() else ""}'
                                       f'{self.get_table().split(".")[-1]}_'
                                       f'{"_".join(self.get_columns().split(COLUMN_DELIMITER))}')
        else:
            return index_name.endswith(f'btree_{self.get_index_type() + "_" if self.get_index_type() else ""}'
                                       f'{self.get_table().replace(".", "_")}_'
                                       f'{"_".join(self.get_columns().split(COLUMN_DELIMITER))}')

    def __str__(self):
        return f'table: {self.__table} columns: {self.__columns} index_type: ' \
               f'{self.__index_type} storage: {self.__storage}'

    def __repr__(self):
        return self.__str__()


def singleton(cls):
    instances = {}

    def _singleton(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    return _singleton


@singleton
class IndexItemFactory:
    def __init__(self):
        self.indexes = {}

    def get_index(self, tbl, cols, index_type):
        if COLUMN_DELIMITER not in cols:
            cols = cols.replace(',', COLUMN_DELIMITER)
        if not (tbl, cols, index_type) in self.indexes:
            self.indexes[(tbl, cols, index_type)] = AdvisedIndex(tbl, cols, index_type=index_type)
        return self.indexes[(tbl, cols, index_type)]


def match_table_name(table_name, tables):
    for elem in tables:
        item_tmp = '_'.join(elem.split('.'))
        if table_name == item_tmp:
            table_name = elem
            break
        elif 'public_' + table_name == item_tmp:
            table_name = 'public.' + table_name
            break
    else:
        return False, table_name
    return True, table_name


class QueryItem:
    __valid_index_list: List[AdvisedIndex]

    def __init__(self, sql: str, freq: float):
        self.__statement = sql
        self.__frequency = freq
        self.__valid_index_list = []
        self.__benefit = 0

    def get_statement(self):
        return self.__statement

    def get_frequency(self):
        return self.__frequency

    def append_index(self, index):
        self.__valid_index_list.append(index)

    def get_indexes(self):
        return self.__valid_index_list

    def reset_opt_indexes(self):
        self.__valid_index_list = []

    def get_sorted_indexes(self):
        return sorted(self.__valid_index_list, key=lambda x: (x.get_table(), x.get_columns(), x.get_index_type()))

    def set_benefit(self, benefit):
        self.__benefit = benefit

    def get_benefit(self):
        return self.__benefit

    def __str__(self):
        return f'statement: {self.get_statement()} frequency: {self.get_frequency()} ' \
               f'index_list: {self.__valid_index_list} benefit: {self.__benefit}'

    def __repr__(self):
        return self.__str__()


class WorkLoad:
    def __init__(self, queries: List[QueryItem]):
        self.__indexes_list = []
        self.__queries = queries
        self.__index_names_list = [[] for _ in range(len(self.__queries))]
        self.__indexes_costs = [[] for _ in range(len(self.__queries))]
        self.__plan_list = [[] for _ in range(len(self.__queries))]

    def get_queries(self) -> List[QueryItem]:
        return self.__queries

    def has_indexes(self, indexes: Tuple[AdvisedIndex]):
        return indexes in self.__indexes_list

    def get_used_index_names(self):
        used_indexes = set()
        for index_names in self.get_workload_used_indexes(None):
            for index_name in index_names:
                used_indexes.add(index_name)
        return used_indexes

    @lru_cache(maxsize=None)
    def get_workload_used_indexes(self, indexes: (Tuple[AdvisedIndex], None)):
        return list([index_names[self.__indexes_list.index(indexes if indexes else None)]
                     for index_names in self.__index_names_list])

    def get_query_advised_indexes(self, indexes, query):
        query_idx = self.__queries.index(query)
        indexes_idx = self.__indexes_list.index(indexes if indexes else None)
        used_index_names = self.__index_names_list[indexes_idx][query_idx]
        used_advised_indexes = []
        for index in indexes:
            for index_name in used_index_names:
                if index.match(index_name):
                    used_advised_indexes.append(index)
        return used_advised_indexes

    def set_index_benefit(self):
        for indexes in self.__indexes_list:
            if indexes and len(indexes) == 1:
                indexes[0].benefit = self.get_index_benefit(indexes[0])

    def replace_indexes(self, origin, new):
        if not new:
            new = None
        self.__indexes_list[self.__indexes_list.index(origin if origin else None)] = new

    @lru_cache(maxsize=None)
    def get_total_index_cost(self, indexes: (Tuple[AdvisedIndex], None)):
        return sum(
            query_index_cost[self.__indexes_list.index(indexes if indexes else None)] for query_index_cost in
            self.__indexes_costs)

    @lru_cache(maxsize=None)
    def get_total_origin_cost(self):
        return self.get_total_index_cost(None)

    @lru_cache(maxsize=None)
    def get_indexes_benefit(self, indexes: Tuple[AdvisedIndex]):
        return self.get_total_origin_cost() - self.get_total_index_cost(indexes)

    @lru_cache(maxsize=None)
    def get_index_benefit(self, index: AdvisedIndex):
        return self.get_indexes_benefit(tuple([index]))

    @lru_cache(maxsize=None)
    def get_indexes_cost_of_query(self, query: QueryItem, indexes: (Tuple[AdvisedIndex], None)):
        return self.__indexes_costs[self.__queries.index(query)][
            self.__indexes_list.index(indexes if indexes else None)]

    @lru_cache(maxsize=None)
    def get_indexes_plan_of_query(self, query: QueryItem, indexes: (Tuple[AdvisedIndex], None)):
        return self.__plan_list[self.__queries.index(query)][
            self.__indexes_list.index(indexes if indexes else None)]

    @lru_cache(maxsize=None)
    def get_origin_cost_of_query(self, query: QueryItem):
        return self.get_indexes_cost_of_query(query, None)

    @lru_cache(maxsize=None)
    def is_positive_query(self, index: AdvisedIndex, query: QueryItem):
        return self.get_origin_cost_of_query(query) > self.get_indexes_cost_of_query(query, tuple([index]))

    def add_indexes(self, indexes: (Tuple[AdvisedIndex], None), costs, index_names, plan_list):
        if not indexes:
            indexes = None
        self.__indexes_list.append(indexes)
        if len(costs) != len(self.__queries):
            raise
        for i, cost in enumerate(costs):
            self.__indexes_costs[i].append(cost)
            self.__index_names_list[i].append(index_names[i])
            self.__plan_list[i].append(plan_list[i])

    @lru_cache(maxsize=None)
    def get_index_related_queries(self, index: AdvisedIndex):
        insert_queries = []
        delete_queries = []
        update_queries = []
        select_queries = []
        positive_queries = []
        ineffective_queries = []
        negative_queries = []

        cur_table = index.get_table()
        for query in self.get_queries():
            if cur_table not in query.get_statement().lower() and \
                    not re.search(r'((\A|[\s(,])%s[\s),])' % cur_table.split('.')[-1],
                                  query.get_statement().lower()):
                continue

            if any(re.match(r'(insert\s+into\s+%s\s)' % table, query.get_statement().lower())
                   for table in [cur_table, cur_table.split('.')[-1]]):
                insert_queries.append(query)
                if not self.is_positive_query(index, query):
                    negative_queries.append(query)
            elif any(re.match(r'(delete\s+from\s+%s\s)' % table, query.get_statement().lower()) or
                     re.match(r'(delete\s+%s\s)' % table, query.get_statement().lower())
                     for table in [cur_table, cur_table.split('.')[-1]]):
                delete_queries.append(query)
                if not self.is_positive_query(index, query):
                    negative_queries.append(query)
            elif any(re.match(r'(update\s+%s\s)' % table, query.get_statement().lower())
                     for table in [cur_table, cur_table.split('.')[-1]]):
                update_queries.append(query)
                if not self.is_positive_query(index, query):
                    negative_queries.append(query)
            else:
                select_queries.append(query)
                if not self.is_positive_query(index, query):
                    ineffective_queries.append(query)
            positive_queries = [query for query in insert_queries + delete_queries + update_queries + select_queries
                                if query not in negative_queries + ineffective_queries]
        return insert_queries, delete_queries, update_queries, select_queries, \
            positive_queries, ineffective_queries, negative_queries

    @lru_cache(maxsize=None)
    def get_index_sql_num(self, index: AdvisedIndex):
        insert_queries, delete_queries, update_queries, \
            select_queries, positive_queries, ineffective_queries, \
            negative_queries = self.get_index_related_queries(index)
        insert_sql_num = sum(query.get_frequency() for query in insert_queries)
        delete_sql_num = sum(query.get_frequency() for query in delete_queries)
        update_sql_num = sum(query.get_frequency() for query in update_queries)
        select_sql_num = sum(query.get_frequency() for query in select_queries)
        positive_sql_num = sum(query.get_frequency() for query in positive_queries)
        ineffective_sql_num = sum(query.get_frequency() for query in ineffective_queries)
        negative_sql_num = sum(query.get_frequency() for query in negative_queries)
        return {'insert': insert_sql_num, 'delete': delete_sql_num, 'update': update_sql_num, 'select': select_sql_num,
                'positive': positive_sql_num, 'ineffective': ineffective_sql_num, 'negative': negative_sql_num}


def get_statement_count(queries: List[QueryItem]):
    return int(sum(query.get_frequency() for query in queries))


def is_subset_index(indexes1: Tuple[AdvisedIndex], indexes2: Tuple[AdvisedIndex]):
    existing = False
    if len(indexes1) > len(indexes2):
        return existing
    for index1 in indexes1:
        existing = False
        for index2 in indexes2:
            # Example indexes1: [table1 col1 global] belong to indexes2:[table1 col1, col2 global].
            if index2.get_table() == index1.get_table() \
                    and match_columns(index1.get_columns(), index2.get_columns()) \
                    and index2.get_index_type() == index1.get_index_type():
                existing = True
                break
        if not existing:
            break
    return existing


def lookfor_subsets_configs(config: List[AdvisedIndex], atomic_config_total: List[Tuple[AdvisedIndex]]):
    """ Look for the subsets of a given config in the atomic configs. """
    contained_atomic_configs = []
    for atomic_config in atomic_config_total:
        if len(atomic_config) == 1:
            continue
        if not is_subset_index(atomic_config, tuple(config)):
            continue
        # Atomic_config should contain the latest candidate_index.
        if not any(is_subset_index((atomic_index,), (config[-1],)) for atomic_index in atomic_config):
            continue
        # Filter redundant config in contained_atomic_configs.
        for contained_atomic_config in contained_atomic_configs[:]:
            if is_subset_index(contained_atomic_config, atomic_config):
                contained_atomic_configs.remove(contained_atomic_config)

        contained_atomic_configs.append(atomic_config)

    return contained_atomic_configs


def match_columns(column1, column2):
    return re.match(column1 + ',', column2 + ',')


def infer_workload_benefit(workload: WorkLoad, config: List[AdvisedIndex],
                           atomic_config_total: List[Tuple[AdvisedIndex]]):
    """ Infer the total cost of queries for a config according to the cost of atomic configs. """
    total_benefit = 0
    atomic_subsets_configs = lookfor_subsets_configs(config, atomic_config_total)
    is_recorded = [True] * len(atomic_subsets_configs)
    for query in workload.get_queries():
        origin_cost_of_query = workload.get_origin_cost_of_query(query)
        if origin_cost_of_query == 0:
            continue
        # When there are multiple indexes, the benefit is the total benefit
        # of the multiple indexes minus the benefit of every single index.
        total_benefit += \
            origin_cost_of_query - workload.get_indexes_cost_of_query(query, (config[-1],))
        for k, sub_config in enumerate(atomic_subsets_configs):
            single_index_total_benefit = sum(origin_cost_of_query -
                                             workload.get_indexes_cost_of_query(query, (index,))
                                             for index in sub_config)
            portfolio_returns = \
                origin_cost_of_query \
                - workload.get_indexes_cost_of_query(query, sub_config) \
                - single_index_total_benefit
            total_benefit += portfolio_returns
            if portfolio_returns / origin_cost_of_query <= 0.01:
                continue
            # Record the portfolio returns of the index.
            association_indexes = ';'.join([str(index) for index in sub_config])
            association_benefit = (query.get_statement(), portfolio_returns / origin_cost_of_query)
            if association_indexes not in config[-1].association_indexes:
                is_recorded[k] = False
                config[-1].set_association_indexes(association_indexes, association_benefit)
                continue
            if not is_recorded[k]:
                config[-1].set_association_indexes(association_indexes, association_benefit)

    return total_benefit


@lru_cache(maxsize=None)
def get_tokens(query):
    return list(sqlparse.parse(query)[0].flatten())


@lru_cache(maxsize=None)
def has_dollar_placeholder(query):
    tokens = get_tokens(query)
    return any(item.ttype is Name.Placeholder for item in tokens)


@lru_cache(maxsize=None)
def get_placeholders(query):
    placeholders = set()
    for item in get_tokens(query):
        if item.ttype is Name.Placeholder:
            placeholders.add(item.value)
    return placeholders


@lru_cache(maxsize=None)
def generate_placeholder_indexes(table_cxt, column):
    indexes = []
    schema_table = f'{table_cxt.schema}.{table_cxt.table}'
    if table_cxt.is_partitioned_table:
        indexes.append(IndexItemFactory().get_index(schema_table, column, 'global'))
        indexes.append(IndexItemFactory().get_index(schema_table, column, 'local'))
    else:
        indexes.append(IndexItemFactory().get_index(schema_table, column, ''))
    return indexes


def replace_comma_with_dollar(query):
    """
    Replacing '?' with '$+Numbers' in SQL:
      input: UPDATE bmsql_customer SET c_balance = c_balance + $1, c_delivery_cnt = c_delivery_cnt + ?
      WHERE c_w_id = $2 AND c_d_id = $3 AND c_id = $4 and c_info = ?;
      output: UPDATE bmsql_customer SET c_balance = c_balance + $1, c_delivery_cnt = c_delivery_cnt + $5
      WHERE c_w_id = $2 AND c_d_id = $3 AND c_id = $4 and c_info = $6;
    note: if track_stmt_parameter is off, all '?' in SQL need to be replaced
    """
    if '?' not in query:
        return query
    max_dollar_number = 0
    dollar_parts = re.findall(r'(\$\d+)', query)
    if dollar_parts:
        max_dollar_number = max(int(item.strip('$')) for item in dollar_parts)
    while '?' in query:
        dollar = "$%s" % (max_dollar_number + 1)
        query = query.replace('?', dollar, 1)
        max_dollar_number += 1
    return query


@lru_cache(maxsize=None)
def is_multi_node(executor):
    sql = "select pg_catalog.count(*) from pgxc_node where node_type='C';"
    for cur_tuple in executor.execute_sqls([sql]):
        if str(cur_tuple[0]).isdigit():
            return int(cur_tuple[0]) > 0


@contextmanager
def hypo_index_ctx(executor):
    yield
    executor.execute_sqls(['SELECT pg_catalog.hypopg_reset_index();'])


def split_integer(m, n):
    quotient = int(m / n)
    remainder = m % n
    if m < n:
        return [1] * m
    if remainder > 0:
        return [quotient] * (n - remainder) + [quotient + 1] * remainder
    if remainder < 0:
        return [quotient - 1] * -remainder + [quotient] * (n + remainder)
    return [quotient] * n


def split_iter(iterable, n):
    size_list = split_integer(len(iterable), n)
    index = 0
    res = []
    for size in size_list:
        res.append(iterable[index:index + size])
        index += size
    return res


def flatten(iterable):
    for _iter in iterable:
        if hasattr(_iter, '__iter__') and not isinstance(_iter, str):
            for item in flatten(_iter):
                yield item
        else:
            yield _iter
