
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
    },
    'media_box': {
        'default': [0, 0, 612, 792]
    }
}


def get_optional_entry(key, val):
    if val is None:
        if 'default' not in OPTIONS[key]:
            raise Exception
        else:
            val = OPTIONS[key]['default']
    if 'options' in OPTIONS[key] and val not in OPTIONS[key]['options']:
        raise Exception
    return val


def get_inherited_entry(key, node, required=False):
    val = getattr(kid, key)
    if val is None:
        if node.parent is not None:
            val = get_inherited_entry(key, node.parent)
        if val is None and required is True:
            raise Exception
    return val


def to_pdf_dict_output(pdf_dict):
    # TODO: make prettier
    output_parts = [
        '<<',
        f"{k} {v}" for k,v in pdf_dict.items(),
        '>>'
    ]
    return '\n'.join(output_parts)


def to_pdf_array_output(pdf_list):
    return ' '.join(['[', *pdf_list, ']'])


class PdfObject:

    def __init__(self, object_number=None, generation_number=None):
        # TODO: validate, autogenerate, add to cross-reference table
        # TODO: probably do this somewhere else to make obj independent of body
        self.object_number = object_number or self.file_body.generate_object_number()
        self.generation_number = generation_number or 0

        # TODO: probably do this somewhere else to make obj independent of body
        self.file_body.add_object(self)

    def to_pdf_output(self):
        output_parts = [
            f"{self.object_number} {self.generation_number} obj"
        ]
        if self.obj_datatype is dict:
            pdf_dict = self.to_dict()
            pdf_dict['/Type'] = self.obj_type
            output_parts.append(to_pdf_dict_output(pdf_dict))

        output_parts.append("endobj")
        return '\n'.join(output_parts)

    def to_pdf_ref_output(self):
        return f"{self.object_number} {self.generation_number} R"


class PdfFile:

    def __init__(self, header, body, cross_reference_table, trailer):
        self.header = header
        self.body = body
        self.cross_reference_table = cross_reference_table
        self.trailer = trailer

    @classmethod
    def build(cls, header=None, body=None, cross_reference_table=None, trailer=None, version=None):
        version = get_optional_entry('version', version)
        return cls(
            header or cls.build_header(version),
            body or cls.build_body(),
            cross_reference_table or cls.build_cross_reference_table(),
            trailer or cls.build_trailer()
        )

    @staticmethod
    def build_header(version):
        return FileHeader(version)

    @staticmethod
    def build_body():
        # TODO
        page_tree_root = PageTreeNode()
        return

    @staticmethod
    def build_cross_reference_table():
        # TODO
        return

    @staticmethod
    def build_trailer():
        # TODO
        return

    def add_page(self):
        # TODO
        page = PageObject()
        return page

    def to_pdf_output(self):
        output_parts = [
            self.header.to_pdf_output(),
            self.body.to_pdf_output(),
            self.cross_reference_table.to_pdf_output(),
            self.trailer.to_pdf_output(),
        ]
        return '\n'.join(output_parts)

    def to_bytes(self):
        # TODO: is utf-8 okay for this?
        # might want to make encoding/compressing/other-filtering a utility
        return self.to_pdf_output().encode()

    def write(self, io_buffer, linearized=False):
        # TODO: possibly make linearized the default to optimize web read performance
        io_buffer.write(self.to_bytes())


class FileBody:

    def __init__(self, document_catalog):
        self.document_catalog = document_catalog
        self.max_object_number = None

    def generate_object_number(self):
        # TODO: rewrite this
        # should interface with the cross-reference table and interpreter-lock the logic
        # this will be very important to get right!
        if self.max_object_number is None:
            object_number = 0
        else:
            object_number = self.max_object_number + 1
        self.max_object_number = object_number
        return object_number


class FileHeader:
    
    def __init__(self, version):
        self.version = version

    def to_pdf_output(self):
        return f"%PDF-{self.version}"


class FileCrossReferenceTable:
    # TODO
    pass


class FileTrailer:
    # TODO
    pass


class DocumentCatalog(PdfObject):

    obj_datatype = dict
    obj_type = 'Catalog'

    def __init__(self,
            page_tree,
            version=None,
            # page_label_tree=None,
            page_layout=None,
            # outline_hierarchy=None,
            # article_threads=None,
            # named_destinations=None,
            # interactive_form=None,
            **kwargs):
        super().__init__(**kwargs)
        self.page_tree = page_tree

        self.version = get_optional_entry('version', version)
        # self.page_label_tree = page_label_tree
        self.page_layout = get_optional_entry('page_layout', page_layout)
        # self.page_mode = None
        # self.outline_hierarchy = outline_hierarchy
        # self.article_threads = article_threads
        # self.named_destinations = named_destinations
        # self.interactive_form = interactive_form

    def __repr__(self):
        return f'{self.__class__.__name__}({self.page_tree}, version={self.version}, page_layout={self.page_layout})'

    def to_dict():
        pdf_dict = {
            '/Pages': self.page_tree.to_pdf_ref_output()
        }
        return pdf_dict


class PageTreeNode(PdfObject):

    obj_datatype = dict
    obj_type = 'Pages'

    def __init__(self, kids, count, parent, **kwargs):
        super().__init__(**kwargs)
        self.kids = kids
        self.count = count
        self.parent = parent

    def to_dict():
        pdf_dict = {
            '/Kids': to_pdf_array_output([k.to_pdf_ref_output() for k in self.kids]),
            '/Count': self.count
        }
        if self.parent is not None:
            pdf_dict['/Parent'] = self.parent.to_pdf_ref_output()
        return pdf_dict


class PageObject(PdfObject):

    obj_datatype = dict
    obj_type = 'Page'

    def __init__(self, parent, resources, media_box, contents=None, **kwargs):
        super().__init__(**kwargs)
        self.parent = parent
        self.resources = resources
        self.media_box = media_box

        self.contents = contents

    def get_inherited(self):
        self.resources = get_inherited_entry('resources', self, required=True)

    def to_dict():
        self.get_inherited()
        pdf_dict = {
            '/Parent': self.parent.to_pdf_ref_output(),
            '/Resources': to_pdf_dict_output(self.resources),
            '/MediaBox': to_pdf_array_output(self.media_box),
        }
        if self.contents is not None:
            pdf_dict['/Contents'] = self.contents.to_pdf_ref_output()
        return pdf_dict
