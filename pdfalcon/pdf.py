
OPTIONS = {
    'version': {
        'default': '1.4',
        'options': {'1.4', '1.5', '1.6', '1.7'}
    },
    'page_layout': {
        'default': 'single_page',
        'options': {
            'single_page',      # Display one page at a time
            'one_column',       # Display the pages in one column 
            'two_column_left',  # Display the pages in two columns, with odd-numbered pages on the left
            'two_column_right', # Display the pages in two columns, with odd-numbered pages on the right
            'two_page_left',    # (PDF 1.5) Display the pages two at a time, with odd-numbered pages on the left
            'two_page_right',   # (PDF 1.5) Display the pages two at a time, with odd-numbered pages on the right
        }
    }
}


def option(category, val):
    if val is None:
        raise Exception
    if val not in OPTIONS[category]['options']:
        raise Exception
    return val


class PdfObject:
    pass


class PDF:

    def __init__(self, document_catalog):
        self.document_catalog = document_catalog

    def build(self):
        pass

    def write(self, io_buffer):
        pass


class DocumentCatalog(PdfObject):

    obj_datatype = dict
    obj_type = 'Catalog'

    def __init__(self,
            page_tree,
            version=OPTIONS['version']['default'],
            # page_label_tree=None,
            page_layout=OPTIONS['page_layout']['default'],
            # outline_hierarchy=None,
            # article_threads=None,
            # named_destinations=None,
            # interactive_form=None
            ):
        self.page_tree = page_tree

        self.version = option('version', version)
        # self.page_label_tree = page_label_tree
        self.page_layout = option('page_layout', page_layout)
        # self.page_mode = None
        # self.outline_hierarchy = outline_hierarchy
        # self.article_threads = article_threads
        # self.named_destinations = named_destinations
        # self.interactive_form = interactive_form

    def __repr__(self):
        return f'{self.__class__.__name__}({self.page_tree}, version={self.version}, page_layout={self.page_layout})'


class BasePageObject(PdfObject):

    def __init__(self, parent=None, kids=None, count=None):
        self.parent = parent
        self.kids = kids or []
        self.count = count
        # self.root_node = None

    def is_root(self):
        return self.parent is None

    def is_leaf(self):
        return len(self.kids) == 0


class PageTreeNode(BasePageObject):

    obj_datatype = dict
    obj_type = 'Pages'


class PageObject(BasePageObject):

    obj_datatype = dict
    obj_type = 'Page'
