import inspect
import unittest

from app.modules.explorer.router import children


class ExplorerRouterPaginationContractTest(unittest.TestCase):
    def test_children_route_exposes_bounded_page_token_contract(self) -> None:
        parameters = inspect.signature(children).parameters

        page_token = parameters["page_token"].default
        page_size = parameters["page_size"].default

        self.assertIsNone(page_token.default)
        self.assertEqual(page_size.default, 100)
        constraints = {type(item).__name__: item for item in page_size.metadata}
        self.assertEqual(constraints["Ge"].ge, 1)
        self.assertEqual(constraints["Le"].le, 200)


if __name__ == "__main__":
    unittest.main()
