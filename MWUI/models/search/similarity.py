# -*- coding: utf-8 -*-
#
#  Copyright 2017 Ramil Nugmanov <stsouko@live.ru>
#  This file is part of MWUI.
#
#  MWUI is free software; you can redistribute it and/or modify
#  it under the terms of the GNU Affero General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Affero General Public License for more details.
#
#  You should have received a copy of the GNU Affero General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.
#


class Similarity(object):
    @classmethod
    def load_tree(cls, reindex=False):
        if cls.__name__ not in cls.__cached_tree:
            tree = cls.__loader()
            if tree:
                cls.__cached_tree[cls.__name__] = tree

        return super(Similarity, cls).__new__(cls)

    __cached_tree = {}

    @classmethod
    def find_similar(cls):
        pass

    @classmethod
    def get_tree(cls):
        return cls.__cached_tree[cls.__class__.__name__]

    @classmethod
    def __loader(cls):
        """
        :param cls: inherited class name
        :return: tree
        """
        cls.select()  #
        tree = 0
        return tree
