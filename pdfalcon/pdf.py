
from io import BytesIO


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
        *[f"{k} {v}" for k,v in pdf_dict.items()],
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
        self.free = False
        self.next_free_object = None

    def attach(self, object_number, generation_number):
        # attached means the object has been associated to the pdf body
        self.object_number = object_number
        self.generation_number = generation_number
        self.attached = True

    def release(self, next_free_object):
        self.next_free_object = next_free_object
        self.free = True

    def format(self):
        if self.attached is False:
            raise Exception
        output_parts = [
            f"{self.object_number} {self.generation_number} obj"
        ]
        if self.obj_datatype is dict:
            pdf_dict = {
                '/Type': self.obj_type
            }
            pdf_dict.update(self.to_dict())
            output_parts.append(to_pdf_dict_output(pdf_dict))

        output_parts.append("endobj")
        return '\n'.join(output_parts)+'\n'

    def to_pdf_ref_output(self):
        # TODO: maybe make ref a separate class
        if self.attached is False:
            raise Exception
        return f"{self.object_number} {self.generation_number} R"


class PdfFile:

    def __init__(self, header, body, cross_reference_table, trailer):
        self.header = header
        self.body = body
        self.cross_reference_table = cross_reference_table
        self.trailer = trailer

    def add_pdf_object(self, pdf_object):
        pdf_object = self.body.add_pdf_object(pdf_object)
        entry = self.cross_reference_table.add_pdf_object(pdf_object)
        return pdf_object, entry

    @classmethod
    def build(cls, header=None, body=None, cross_reference_table=None, trailer=None, version=None):
        # create the basic structure of a pdf file
        version = get_optional_entry('version', version)
        header = header or FileHeader(version)
        body = body or FileBody()
        cross_reference_table = cross_reference_table or FileCrossReferenceTable()
        cross_reference_table.add_pdf_object(body.zeroth_object)
        cross_reference_table.add_pdf_object(body.document_catalog)
        cross_reference_table.add_pdf_object(body.page_tree_root)
        trailer = trailer or FileTrailer(cross_reference_table, body.document_catalog)
        return cls(header, body, cross_reference_table, trailer)

    def add_page(self):
        # TODO
        page = PageObject()
        return page

    def format(self):
        header = self.header.format()
        body, byte_offset_object_map, crt_byte_offset = self.body.format(len(header))
        output_parts = [
            header,
            body,
            self.cross_reference_table.format(byte_offset_object_map),
            self.trailer.format(crt_byte_offset),
        ]
        return '\n'.join(output_parts)

    def write(self, io_buffer, linearized=False):
        # TODO: encoding must be handled specially based on the objects being used in PDF
        # the encoding will also determine how the cross-reference table and trailer get built
        # might want to make encoding/compressing/other-filtering a utility
        # possibly make linearized the default to optimize web read performance
        if not isinstance(io_buffer, BytesIO):
            raise Exception
        io_buffer.write()


class FileBody:

    def __init__(self):
        self.objects = {}

        # the zeroth object
        self.zeroth_object = PdfObject()
        self.add_pdf_object(self.zeroth_object)
        self.zeroth_object.release(self.zeroth_object)
        self.free_object_list_tail = self.zeroth_object

        # the document catalog object
        self.page_tree_root = PageTreeNode([], 0, None)
        self.document_catalog = DocumentCatalog(self.page_tree_root)
        self.add_pdf_object(self.document_catalog)
        self.add_pdf_object(self.page_tree_root)

    def add_pdf_object(self, pdf_object):
        if pdf_object.attached is True:
            object_number = pdf_object.object_number
            generation_number = pdf_object.generation_number
            if object_number is None:
                raise Exception
            if generation_number is None:
                raise Exception
            if generation_number == 65535:
                raise Exception
            generation_number += 1
        else:
            if len(self.objects) > 0:
                max_object_number, _ = sorted(self.objects)[-1]
                object_number = max_object_number + 1
                generation_number = 0
            else:
                # the zeroth object
                object_number = 0
                generation_number = 65535
        self.objects[(object_number, generation_number)] = pdf_object
        pdf_object.attach(object_number, generation_number)
        return pdf_object

    def release_pdf_object(self, pdf_object):
        # produces a free object
        pdf_object.release(self.zeroth_object)
        # set previous tail's next free object
        self.free_object_list_tail.next_free_object = pdf_object
        # set new tail
        self.free_object_list_tail = pdf_object
        return pdf_object

    def format(self, byte_offset):
        output_parts = []
        byte_offset_object_map = {}
        for k in sorted(self.objects):
            pdf_object = self.objects[k]
            if pdf_object.free is False:
                formatted_object = pdf_object.format()
                byte_offset_object_map[(pdf_object.object_number, pdf_object.generation_number)] = byte_offset
                byte_offset += len(formatted_object)
                output_parts.append(formatted_object)
        return '\n'.join(output_parts), byte_offset_object_map, byte_offset


class FileHeader:
    
    def __init__(self, version):
        self.version = version

    def format(self):
        return f"%PDF-{self.version}"


class FileCrossReferenceTable:

    def __init__(self):
        self.subsections = None
        self.current_crt_subsection_index = None

    def add_pdf_object(self, pdf_object):
        if self.subsections is None or self.current_crt_subsection_index is None:
            self.subsections = [CRTSubsection()]
            self.current_crt_subsection_index = 0
        subsection = self.subsections[self.current_crt_subsection_index]
        entry = CrossReferenceEntry(pdf_object)
        subsection.entries.append(entry)
        return entry

    def format(self, byte_offset_object_map):
        output_parts = ['xref']
        output_parts.extend([subsection.format(byte_offset_object_map) for subsection in self.subsections])
        return '\n'.join(output_parts)


class CRTSubsection:

    def __init__(self):
        self.entries = []

    def format(self, byte_offset_object_map):
        if len(self.entries) == 0:
            raise Exception
        first_object_number = self.entries[0].pdf_object.object_number
        output_parts = [f"{first_object_number} {len(self.entries)}"]
        output_parts.extend([entry.format(byte_offset_object_map) for entry in self.entries])
        return '\n'.join(output_parts)


class CrossReferenceEntry:

    def __init__(self, pdf_object):
        self.pdf_object = pdf_object

    def format(self, byte_offset_object_map):
        if self.pdf_object.free is True:
            first_item = self.pdf_object.next_free_object.object_number
            generation_number = self.pdf_object.generation_number
            if generation_number != 65535:
                # next generation number should this object be used again
                generation_number += 1
        else:
            first_item = byte_offset_object_map[(self.pdf_object.object_number, self.pdf_object.generation_number)]
            generation_number = self.pdf_object.generation_number
        return f"{first_item:010} {generation_number:05} {'f' if self.pdf_object.free is True else 'n'}"


class FileTrailer:

    def __init__(self, cross_reference_table, document_catalog):
        self.cross_reference_table = cross_reference_table
        self.document_catalog = document_catalog

    def format(self, crt_byte_offset):
        output_parts = ['trailer']
        pdf_dict = {
            '/Root': self.document_catalog.to_pdf_ref_output()
        }
        output_parts.append(to_pdf_dict_output(pdf_dict))
        output_parts.append('startxref')
        output_parts.append(str(crt_byte_offset))
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

    def to_dict(self):
        pdf_dict = {
            '/Pages': self.page_tree.to_pdf_ref_output()
        }
        return pdf_dict


class PageTreeNode(PdfObject):

    obj_datatype = dict
    obj_type = 'Pages'

    def __init__(self, kids, count, parent):
        super().__init__()
        self.kids = kids
        self.count = count
        self.parent = parent

        self.resources = 'meow'

    def to_dict(self):
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

    def __init__(self, parent, resources, media_box, contents=None):
        super().__init__()
        self.parent = parent
        self.resources = resources
        self.media_box = media_box

        self.contents = contents

    def get_inherited(self):
        self.resources = get_inherited_entry('resources', self, required=True)

    def to_dict(self):
        self.get_inherited()
        pdf_dict = {
            '/Parent': self.parent.to_pdf_ref_output(),
            '/Resources': to_pdf_dict_output(self.resources),
            '/MediaBox': to_pdf_array_output(self.media_box),
        }
        if self.contents is not None:
            pdf_dict['/Contents'] = self.contents.to_pdf_ref_output()
        return pdf_dict
