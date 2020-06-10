import math

from io import BytesIO


OPTIONS = {
    'version': {
        'default': '1.4',
        'options': {'1.4': {}, '1.5': {}, '1.6': {}, '1.7': {}}
    },
    'page_layout': {
        'default': 'single_page',
        'options': {
            'single_page': {},      # Display one page at a time
            'one_column': {},       # Display the pages in one column 
            'two_column_left': {},  # Display the pages in two columns, with odd-numbered pages on the left
            'two_column_right': {}, # Display the pages in two columns, with odd-numbered pages on the right
            'two_page_left': {},    # (PDF 1.5) Display the pages two at a time, with odd-numbered pages on the left
            'two_page_right': {},   # (PDF 1.5) Display the pages two at a time, with odd-numbered pages on the right
        }
    },
    'media_box': {
        'default': [0, 0, 612, 792]
    },
    'font': {
        'default': 'Helvetica',
        'options': {
            'Times-Roman': {
                'sub_type': 'Type1'
            },
            'Helvetica': {
                'sub_type': 'Type1'
            },
            'Courier': {
                'sub_type': 'Type1'
            },
            'Symbol': {
                'sub_type': 'Type1'
            },
            'Times-Bold': {
                'sub_type': 'Type1'
            },
            'Helvetica-Bold': {
                'sub_type': 'Type1'
            },
            'Courier-Bold': {
                'sub_type': 'Type1'
            },
            'ZapfDingbats': {
                'sub_type': 'Type1'
            },
            'Times-Italic': {
                'sub_type': 'Type1'
            },
            'Helvetica-Oblique': {
                'sub_type': 'Type1'
            },
            'Courier-Oblique': {
                'sub_type': 'Type1'
            },
            'Times-BoldItalic': {
                'sub_type': 'Type1'
            },
            'Helvetica-BoldOblique': {
                'sub_type': 'Type1'
            },
            'Courier-BoldOblique': {
                'sub_type': 'Type1'
            },
        }
    },
}

def get_optional_entry(key, val):
    if val is None:
        if 'default' not in OPTIONS[key]:
            raise Exception
        else:
            val = OPTIONS[key]['default']
    if 'options' in OPTIONS[key] and val not in OPTIONS[key]['options']:
        raise Exception
    settings = OPTIONS[key]['options'][val] if 'options' in OPTIONS[key] else {}
    return val, settings


def get_inherited_entry(key, node, required=False):
    val = getattr(node, key)
    if val is None:
        if node.parent is not None:
            val = get_inherited_entry(key, node.parent)
        if val is None and required is True:
            raise Exception
    return val


def format_type(data):
    if isinstance(data, list):
        return ' '.join(['[', *[format_type(x) for x in data], ']'])
    elif isinstance(data, dict):
        output_lines = [
            '<<',
            *[f"{k} {format_type(v)}" for k,v in data.items()],
            '>>'
        ]
        return '\n'.join(output_lines)
    elif isinstance(data, int) or isinstance(data, float):
        return str(data)
    else:
        return data


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
        output_lines = [
            f"{self.object_number} {self.generation_number} obj"
        ]
        if self.obj_datatype == 'dictionary':
            pdf_dict = {
                '/Type': self.obj_type
            }
            pdf_dict.update(self.to_dict())
            output_lines.append(format_type(pdf_dict))
        elif self.obj_datatype == 'stream':
            output_lines.extend([
                'stream',
                self.to_stream(),
                'endstream'
            ])

        output_lines.append("endobj")
        return '\n'.join(output_lines)+'\n'

    def format_ref(self):
        # TODO: maybe make ref a separate class
        if self.attached is False:
            raise Exception
        return f"{self.object_number} {self.generation_number} R"


class PdfFile:

    def __init__(self, header=None, body=None, cross_reference_table=None, trailer=None, version=None):
        self.version, _ = get_optional_entry('version', version)
        self.header = header or FileHeader(self)
        self.body = body or FileBody(self)
        self.cross_reference_table = cross_reference_table or FileCrossReferenceTable(self)
        self.trailer = trailer or FileTrailer(self)

    def add_pdf_object(self, pdf_object):
        pdf_object = self.body.add_pdf_object(pdf_object)
        entry = self.cross_reference_table.add_pdf_object(pdf_object)
        return pdf_object, entry

    def add_page(self):
        page = self.body.page_tree_root.add_page()
        self.add_pdf_object(page)
        return page

    def format(self):
        header = self.header.format()
        body, byte_offset_object_map, crt_byte_offset = self.body.format(len(header)+1)
        output_lines = [
            header,
            body,
            self.cross_reference_table.format(byte_offset_object_map),
            self.trailer.format(crt_byte_offset),
        ]
        return '\n'.join(output_lines)

    def write(self, io_buffer, linearized=False):
        # TODO: encoding must be handled specially based on the objects being used in PDF
        # the encoding will also determine how the cross-reference table and trailer get built
        # might want to make encoding/compressing/other-filtering a utility
        # possibly make linearized the default to optimize web read performance
        if not isinstance(io_buffer, BytesIO):
            raise Exception
        io_buffer.write()


class FileBody:

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file
        self.objects = {}
        self.fonts = {}

        # the zeroth object
        self.zeroth_object = PdfObject()
        self.add_pdf_object(self.zeroth_object)
        self.zeroth_object.release(self.zeroth_object)
        self.free_object_list_tail = self.zeroth_object

        # the document catalog object
        self.page_tree_root = PageTreeNode(self.pdf_file)
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
        if isinstance(pdf_object, Font):
            self.fonts[pdf_object.font_name] = pdf_object
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
        output_lines = []
        byte_offset_object_map = {}
        for k in sorted(self.objects):
            pdf_object = self.objects[k]
            if pdf_object.free is False:
                formatted_object = pdf_object.format()
                byte_offset_object_map[(pdf_object.object_number, pdf_object.generation_number)] = byte_offset
                byte_offset += len(formatted_object)+1
                output_lines.append(formatted_object)
        return '\n'.join(output_lines), byte_offset_object_map, byte_offset


class FileHeader:
    
    def __init__(self, pdf_file):
        self.pdf_file = pdf_file

    def format(self):
        output_lines = [
            f"%PDF-{self.pdf_file.version}",
            "%âãÏÓ"
        ]
        return '\n'.join(output_lines)


class FileCrossReferenceTable:

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file
        self.subsections = None
        self.current_crt_subsection_index = None

        self.add_pdf_object(self.pdf_file.body.zeroth_object)
        self.add_pdf_object(self.pdf_file.body.document_catalog)
        self.add_pdf_object(self.pdf_file.body.page_tree_root)

    def add_pdf_object(self, pdf_object):
        if self.subsections is None or self.current_crt_subsection_index is None:
            self.subsections = [CRTSubsection()]
            self.current_crt_subsection_index = 0
        subsection = self.subsections[self.current_crt_subsection_index]
        entry = CrossReferenceEntry(pdf_object)
        subsection.entries.append(entry)
        return entry

    def format(self, byte_offset_object_map):
        output_lines = ['xref']
        output_lines.extend([subsection.format(byte_offset_object_map) for subsection in self.subsections])
        return '\n'.join(output_lines)


class CRTSubsection:

    def __init__(self):
        self.entries = []

    def format(self, byte_offset_object_map):
        if len(self.entries) == 0:
            raise Exception
        first_object_number = self.entries[0].pdf_object.object_number
        output_lines = [f"{first_object_number} {len(self.entries)}\n"]
        output_lines.extend([entry.format(byte_offset_object_map) for entry in self.entries])
        return ''.join(output_lines)


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
        return f"{first_item:010} {generation_number:05} {'f' if self.pdf_object.free is True else 'n'} \n"


class FileTrailer:

    def __init__(self, pdf_file):
        self.pdf_file = pdf_file

    def format(self, crt_byte_offset):
        output_lines = ['trailer']
        pdf_dict = {
            '/Root': self.pdf_file.body.document_catalog.format_ref(),
            '/Size': sum(len(s.entries) for s in self.pdf_file.cross_reference_table.subsections)
        }
        output_lines.extend([
            format_type(pdf_dict),
            'startxref',
            str(crt_byte_offset),
            '%%EOF'
        ])
        return '\n'.join(output_lines)


class DocumentCatalog(PdfObject):

    obj_datatype = 'dictionary'
    obj_type = '/Catalog'

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

        self.version, _ = get_optional_entry('version', version)
        # self.page_label_tree = page_label_tree
        self.page_layout, _ = get_optional_entry('page_layout', page_layout)
        # self.page_mode = None
        # self.outline_hierarchy = outline_hierarchy
        # self.article_threads = article_threads
        # self.named_destinations = named_destinations
        # self.interactive_form = interactive_form

    def __repr__(self):
        return f'{self.__class__.__name__}({self.page_tree}, version={self.version}, page_layout={self.page_layout})'

    def to_dict(self):
        pdf_dict = {
            '/Pages': self.page_tree.format_ref()
        }
        return pdf_dict


class PageTreeNode(PdfObject):

    obj_datatype = 'dictionary'
    obj_type = '/Pages'

    def __init__(self, pdf_file, parent=None):
        super().__init__()
        self.pdf_file = pdf_file
        self.parent = parent
        self.kids = []
        self.count = 0

        # inheritable properties
        self.resources = {'/Font': {}}
        self.media_box, _ = get_optional_entry('media_box', None)

    def to_dict(self):
        pdf_dict = {
            '/Kids': format_type([k.format_ref() for k in self.kids]),
            '/Count': self.count
        }
        if self.parent is not None:
            pdf_dict['/Parent'] = self.parent.format_ref()
        return pdf_dict

    def add_page(self):
        page = PageObject(self.pdf_file, self)
        self.kids.append(page)
        self.count += 1
        return page


class PageObject(PdfObject):

    obj_datatype = 'dictionary'
    obj_type = '/Page'

    def __init__(self, pdf_file, parent, resources=None, media_box=None, contents=None):
        super().__init__()
        self.pdf_file = pdf_file
        self.parent = parent

        self.font_mapping = {}
        self.resources = resources
        self.media_box = media_box
        self.contents = contents or []
        self.get_inherited()

    def get_inherited(self):
        self.resources = get_inherited_entry('resources', self, required=True)
        self.media_box = get_inherited_entry('media_box', self, required=True)

    def add_content_stream(self):
        stream = ContentStream()
        self.pdf_file.add_pdf_object(stream)
        self.contents.append(stream)
        return stream

    def add_text(self, text, font_name=None):
        font_name, font_settings = get_optional_entry('font', font_name)
        if font_name not in self.pdf_file.body.fonts:
            font = Font(font_name, font_settings['sub_type'])
            self.pdf_file.add_pdf_object(font)
        else:
            font = self.pdf_file.body.fonts[font_name]
        font_number = max(self.font_mapping)+1 if len(self.font_mapping) > 0 else 1
        self.font_mapping[font_number] = font
        font_alias_name = f'/F{font_number}'
        self.resources['/Font'][font_alias_name] = font.format_ref()

        stream = self.add_content_stream()
        stream.add_content([GraphicsStateTransition().format(TextObject(text, font_alias_name).format())])
        return stream

    def to_dict(self):
        pdf_dict = {
            '/Parent': self.parent.format_ref(),
            '/Resources': format_type(self.resources),
            '/MediaBox': format_type(self.media_box),
        }
        if self.contents is not None:
            pdf_dict['/Contents'] = format_type([content.format_ref() for content in self.contents])
        return pdf_dict


class Font(PdfObject):

    obj_datatype = 'dictionary'
    obj_type = '/Font'

    def __init__(self, font_name, sub_type):
        super().__init__()
        self.font_name = font_name
        self.sub_type = sub_type

    def to_dict(self):
        pdf_dict = {
            '/SubType': f"/{self.sub_type}",
            '/BaseFont': f"/{self.font_name}",
        }
        return pdf_dict


class ContentStream(PdfObject):

    obj_datatype = 'stream'

    def __init__(self):
        super().__init__()
        self.stream_content = []

    def add_content(self, content):
        self.stream_content.extend(content)

    def to_stream(self):
        return '\n'.join([content.format() for content in self.stream_content])


class GraphicsStateTransition:

    def __init__(self,
            translate_x=None, translate_y=None,
            scale_x=None, scale_y=None,
            skew_angle_x=None, skew_angle_y=None,
            rotation_angle=None):
        # transformations should be done in the following order:
        # Translate, Rotate, Scale or Skew
        self.scale_x = scale_x or 1
        self.skew_x = math.tan(skew_angle_x) if skew_angle_x else 0
        self.skew_y = math.tan(skew_angle_y) if skew_angle_y else 0
        self.scale_y = scale_y or 1
        self.translate_x = translate_x or 0
        self.translate_y = translate_y or 0
        if rotation_angle is not None:
            self.scale_x = math.cos(self.scale_x)
            self.skew_x = math.sin(self.skew_x)
            self.skew_y = -math.sin(self.skew_y)
            self.scale_y = math.cos(self.scale_y)
        self.transformation_matrix = [
            self.scale_x,
            self.skew_x,
            self.skew_y,
            self.scale_y,
            self.translate_x,
            self.translate_y,
        ]

    def format(self, content_str):
        output_ops = [
            'q',
            f"{' '.join(map(lambda x: str(x), self.transformation_matrix))} cm",
            content_str,
            'Q'
        ]
        return '\n'.join(output_ops)


class TextObject:

    def __init__(self, text, font,
            size=None, line_size=None,
            text_transformation_matrix=None, text_color_matrix=None,
            **kwargs):
        self.text = text
        self.font = font
        self.size = size or 12
        self.line_size = line_size or 14.4
        self.text_transformation_matrix = text_transformation_matrix or [1, 0, 0, 1, 0, 0]
        self.text_color_matrix = text_color_matrix or [0, 0, 0]

    def format(self):
        output_ops = [
            'BT ',
            f"{' '.join(map(lambda x: str(x), self.text_transformation_matrix))} Tm ",
            f"{self.font} {self.size} Tf ",
            f"{self.line_size} TL ",
            f"({self.text}) Tj ",
            'T* ',
            'ET'
        ]
        return ''.join(output_ops)
