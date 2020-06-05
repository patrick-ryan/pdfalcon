
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

    def __init__(self):
        self.object_number = None
        self.generation_number = None
        self.attached = False
        self.output = None

    def attach(self, object_number, generation_number):
        self.object_number = object_number
        self.generation_number = generation_number

        self.output = self.to_file_output()
        self.attached = True

    def to_file_output(self):
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

    def add_pdf_object(self, pdf_object, is_free=False):
        pdf_object = self.body.add_pdf_object(pdf_object)
        entry = self.cross_reference_table.add_pdf_object(pdf_object, is_free=is_free)
        return pdf_object, entry

    @classmethod
    def build(cls, header=None, body=None, cross_reference_table=None, trailer=None, version=None):
        version = get_optional_entry('version', version)
        header = header or cls.build_header(version)
        body = body or cls.build_body()

        page_tree_root = PageTreeNode([], 0, None)
        document_catalog = DocumentCatalog(page_tree_root)
        body.add_pdf_object(document_catalog)

        cross_reference_table = cross_reference_table or cls.build_cross_reference_table()
        cross_reference_table.add_pdf_object(document_catalog)

        trailer = trailer or cls.build_trailer(cross_reference_table, document_catalog)

        return cls(header, body, cross_reference_table, trailer)

    @staticmethod
    def build_header(version):
        return FileHeader(version)

    @staticmethod
    def build_body():
        return FileBody()

    @staticmethod
    def build_cross_reference_table():
        return FileCrossReferenceTable([CRTSubsection([CrossReferenceEntry(0, 65535, True)], 0)])

    @staticmethod
    def build_trailer(cross_reference_table, document_catalog):
        return FileTrailer(cross_reference_table, document_catalog)

    def add_page(self):
        # TODO
        page = PageObject()
        return page

    def to_file_output(self):
        output_parts = [
            self.header.output,
            self.body.output,
            self.cross_reference_table.output,
            self.trailer.output,
        ]
        return '\n'.join(output_parts)

    def to_bytes(self):
        # TODO: is utf-8 okay for this?
        # might want to make encoding/compressing/other-filtering a utility
        return self.output.encode()

    def write(self, io_buffer, linearized=False):
        # TODO: possibly make linearized the default to optimize web read performance
        io_buffer.write(self.to_bytes())


class FileBody:

    def __init__(self):
        self.objects = {}
        self.max_object_number = None

        self.output = self.to_file_output()

    def add_pdf_object(self, pdf_object):
        if pdf_object.attached is True:
            raise Exception
        object_number = self.generate_object_number()
        if object_number in self.objects:
            generation_number = max(self.objects[object_number])+1
        else:
            self.objects[object_number] = {}
            generation_number = 0
        self.objects[object_number][generation_number] = pdf_object
        pdf_object.attach(object_number, generation_number)
        return pdf_object

    def generate_object_number(self):
        # TODO: rewrite this
        if self.max_object_number is None:
            self.max_object_number = 0
        self.max_object_number += 1
        return self.max_object_number

    def to_file_output(self):
        sorted_objects = []
        for object_number in sorted(self.objects):
            for generation_number in sorted(self.objects[object_number]):
                sorted_objects.append(self.objects[object_number][generation_number])
        return '\n'.join([pdf_object.output for pdf_object in sorted_objects])


class FileHeader:
    
    def __init__(self, version):
        self.version = version

        self.output = self.to_file_output()

    def to_file_output(self):
        return f"%PDF-{self.version}"


class FileCrossReferenceTable:

    def __init__(self, subsections):
        self.subsections = subsections

        self.current_crt_subsection_index = 0
        self.next_object_byte_offset = None

        self.output = self.to_file_output()

    def add_pdf_object(self, pdf_object, is_free=False):
        subsection = self.subsections[self.current_crt_subsection_index]
        if self.next_object_byte_offset is None:
            self.next_object_byte_offset = len(self.header.output)
        byte_offset = self.next_object_byte_offset
        self.next_object_byte_offset += len(pdf_object.output)
        entry = CrossReferenceEntry(byte_offset, pdf_object.generation_number, is_free)
        subsection.entries.append(entry)
        return entry

    def to_file_output(self):
        return f"{self.first_object_number} {len(self.entries)}\n"
            '\n'.join([subsection.output for subsection in self.subsections])


class CRTSubsection:

    def __init__(self, entries, first_object_number):
        self.entries = entries
        self.first_object_number = first_object_number

        self.output = self.to_file_output()

    def to_file_output(self):
        return f"{self.first_object_number} {len(self.entries)}\n"
            '\n'.join([entry.output for entry in self.entries])


class CrossReferenceEntry:

    def __init__(self, byte_offset, generation_number, is_free):
        self.byte_offset = byte_offset
        self.generation_number = generation_number
        self.is_free = is_free

        self.output = self.to_file_output()

    def to_file_output(self):
        return f"{self.byte_offset:010} {self.generation_number:05} {'f' if self.is_free is True else 'n'}"


class FileTrailer:

    def __init__(self, cross_reference_table, document_catalog):
        self.cross_reference_table = cross_reference_table
        self.document_catalog = document_catalog

        self.output = self.to_file_output()

    def to_file_output(self):
        output_parts = ['trailer']
        pdf_dict = {
            '/Root': self.document_catalog.to_pdf_ref_output()
        }
        output_parts.append(to_pdf_dict_output(pdf_dict))
        output_parts.append('startxref')
        # TODO
        output_parts.append('<cross_reference_table byte offset>')
        output_parts.append('%%EOF')
        return '\n'.join(output_parts)


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
            ):
        super().__init__()
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
        super().__init__()
        self.kids = kids
        self.count = count
        self.parent = parent

        self.resources = resources

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
        super().__init__()
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
