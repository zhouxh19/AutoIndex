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

import os
import re
import time


try:
    TERMINAL_SIZE = os.get_terminal_size().columns
except (AttributeError, OSError):
    TERMINAL_SIZE = 60


class ProcessBar:
    LENGTH = TERMINAL_SIZE - 40

    def __init__(self):
        self.start = time.perf_counter()
        self.iterable = None
        self.title = None
        self.percent = 0
        self.process_num = 0

    def process_bar(self, iterable, title):
        self.iterable = iterable
        self.title = title
        self.percent = 0
        self.process_num = 0
        return self

    def __get_time(self):
        return time.perf_counter() - self.start

    def __processbar(self):
        bar_print(self.__output())

    def __output(self):
        return "{}: {:^3.0f}%[{}{}]{:.2f}s".format(self.title, self.percent * 100,
                                                   '>' * int(self.percent * ProcessBar.LENGTH),
                                                   '*' * (ProcessBar.LENGTH - int(self.percent * ProcessBar.LENGTH)),
                                                   self.__get_time())

    @staticmethod
    def match(content):
        p = re.compile('[*>]+')
        res = p.search(str(content))
        if res:
            return len(res.group()) == ProcessBar.LENGTH

    def __iter__(self):
        return self

    def __next__(self):
        self.process_num += 1
        if self.process_num > len(self.iterable):
            raise StopIteration
        self.percent = self.process_num / len(self.iterable)
        self.__processbar()

        return self.iterable[self.process_num - 1]


def _print_wrap():
    last_content = ''

    def inner_bar_print(*content):
        nonlocal last_content
        if ProcessBar.match(last_content):
            print(f'\x1b[1A{" " * TERMINAL_SIZE}\r')
            size = len(content)
            print('\x1b[1A' + (' '.join(['{}'] * size)).format(*content))
            if ProcessBar.match(content[0]):
                last_content = content[0]
            else:
                print(last_content)
        else:
            if ProcessBar.match(content[0]):
                print(content[0])
                last_content = content[0]

    return inner_bar_print


bar_print = _print_wrap()
