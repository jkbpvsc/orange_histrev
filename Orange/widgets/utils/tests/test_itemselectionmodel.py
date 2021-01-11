from unittest import TestCase
from typing import Tuple, Set

from AnyQt.QtCore import QItemSelectionModel
from AnyQt.QtGui import QStandardItemModel

from Orange.widgets.utils.itemselectionmodel import BlockSelectionModel


def selected(sel: QItemSelectionModel) -> Set[Tuple[int, int]]:
    return set((r.row(), r.column()) for r in sel.selectedIndexes())


class BlockSelectionModelTest(TestCase):
    def test_blockselectionmodel(self):
        model = QStandardItemModel()
        model.setRowCount(4)
        model.setColumnCount(4)
        sel = BlockSelectionModel(model)
        sel.select(model.index(0, 0), BlockSelectionModel.Select)
        self.assertSetEqual(selected(sel), {(0, 0)})
        sel.select(model.index(0, 1), BlockSelectionModel.Select)
        self.assertSetEqual(selected(sel), {(0, 0), (0, 1)})
        sel.select(model.index(1, 1), BlockSelectionModel.Select)
        self.assertSetEqual(selected(sel), {(0, 0), (0, 1), (1, 0), (1, 1)})
        sel.select(model.index(0, 0), BlockSelectionModel.Deselect)
        self.assertSetEqual(selected(sel), {(1, 1)})
        sel.select(model.index(3, 3), BlockSelectionModel.ClearAndSelect)
        self.assertSetEqual(selected(sel), {(3, 3)})
