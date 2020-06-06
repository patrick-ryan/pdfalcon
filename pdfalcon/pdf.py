
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
        # TODO: determine best way to set this, might be better removed and left to the writing process to figure out
        self.byte_offset = None
        self.free = False

    def attach(self, object_number, generation_number):
        self.object_number = object_number
        self.generation_number = generation_number
        self.attached = True

    def format(self):
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
        # create the basic structure of a pdf file
        version = get_optional_entry('version', version)
        header = header or FileHeader(version)

        body = body or FileBody()
        page_tree_root = PageTreeNode([], 0, None)
        document_catalog = DocumentCatalog(page_tree_root)
        body.add_pdf_object(document_catalog)

        free_object = PdfObject()
        free_object.object_number = 0
        free_object.generation_number = 65535
        free_object.byte_offset = 0
        free_object.free = True
        first_entry = CrossReferenceEntry(free_object)
        subsections = [CRTSubsection([first_entry])]
        cross_reference_table = cross_reference_table or FileCrossReferenceTable(subsections)
        cross_reference_table.add_pdf_object(document_catalog)

        trailer = trailer or FileTrailer(cross_reference_table, document_catalog)

        return cls(header, body, cross_reference_table, trailer)

    def add_page(self):
        # TODO
        page = PageObject()
        return page

    def format(self):
        self.header.format()
        output_parts = [
            ,
            self.body.format(),
            self.cross_reference_table.format(),
            self.trailer.format(),
        ]
        return '\n'.join(output_parts)

    def write(self, io_buffer, linearized=False):
        # TODO: encoding must be handled specially based on the objects being used in PDF
        # the encoding will also determine how the cross-reference table and trailer get built
        # might want to make encoding/compressing/other-filtering a utility
        # possibly make linearized the default to optimize web read performance
        io_buffer.write()


class FileBody:

    def __init__(self):
        self.objects = {}

        # dynamically determined based on size of header
        self.byte_offset = None

    def add_pdf_object(self, pdf_object):
        if pdf_object.attached is True:
            raise Exception
        object_number = max(self.objects)+1
        if object_number in self.objects:
            generation_number = max(self.objects[object_number])+1
        else:
            self.objects[object_number] = {}
            generation_number = 0
        self.objects[object_number][generation_number] = pdf_object
        pdf_object.attach(object_number, generation_number)
        return pdf_object

    def format(self):
        sorted_objects = []
        for object_number in sorted(self.objects):
            for generation_number in sorted(self.objects[object_number]):
                sorted_objects.append(self.objects[object_number][generation_number])
        return '\n'.join([pdf_object.format() for pdf_object in sorted_objects])


class FileHeader:
    
    def __init__(self, version):
        self.version = version

    def format(self):
        return f"%PDF-{self.version}"


class FileCrossReferenceTable:

    def __init__(self, subsections):
        self.subsections = subsections

        self.current_crt_subsection_index = 0
        self.next_object_byte_offset = None

    def add_pdf_object(self, pdf_object):
        subsection = self.subsections[self.current_crt_subsection_index]
        entry = CrossReferenceEntry(pdf_object)
        subsection.entries.append(entry)
        return entry

    def format(self):
        return "xref"
            '\n'.join([subsection.format() for subsection in self.subsections])


class CRTSubsection:

    def __init__(self, entries):
        self.entries = entries

    def format(self):
        if len(self.entries) == 0:
            raise Exception
        first_object_number = self.entries[0].pdf_object.object_number
        return f"{first_object_number} {len(self.entries)}\n"
            '\n'.join([entry.format() for entry in self.entries])


class CrossReferenceEntry:

    def __init__(self, pdf_object):
        self.pdf_object = pdf_object

    def format(self):
        if self.pdf_object.byte_offset is None:
            raise Exception
        return f"{self.pdf_object.byte_offset:010} {self.pdf_object.generation_number:05} {'f' if self.pdf_object.free is True else 'n'}"


class FileTrailer:

    def __init__(self, cross_reference_table, document_catalog):
        self.cross_reference_table = cross_reference_table
        self.document_catalog = document_catalog

    def format(self):
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
