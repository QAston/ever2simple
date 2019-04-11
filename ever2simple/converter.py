import json
import os
import sys
import io
from csv import DictWriter
from dateutil.parser import parse
from html2text import HTML2Text
from lxml import etree


class EverConverter(object):
    """Evernote conversion runner
    """

    fieldnames = ['createdate', 'modifydate', 'content', 'tags', 'resources']
    date_fmt = '%Y %m %d %H:%M:%S'

    def __init__(self, enex_filename, simple_filename, fmt, metadata):
        self.enex_filename = os.path.expanduser(enex_filename)
        self.stdout = False
        if simple_filename is None:
            self.stdout = True
            self.simple_filename = simple_filename
        else:
            self.simple_filename = os.path.expanduser(simple_filename)
        self.fmt = fmt
        self.metadata = metadata

    def _load_xml(self, enex_file):
        try:
            parser = etree.XMLParser(huge_tree=True)
            xml_tree = etree.parse(enex_file, parser)
        except etree.XMLSyntaxError as e:
            print('Could not parse XML')
            print(e)
            sys.exit(1)
        return xml_tree

    def prepare_notes(self, xml_tree):
        notes = []
        raw_notes = xml_tree.xpath('//note')
        for note in raw_notes:

            note_dict = {}
            title = note.xpath('title')[0].text
            note_dict['title'] = title
            # Use dateutil to figure out these dates
            # 20110610T182917Z
            resources = []
            for resource in note.xpath("resource"):
                mime = resource.xpath("mime")[0].text
                if mime == "image/png" or "image/jpeg":
                    try:
                        r_title = resource.xpath("resource-attributes")[0].xpath("file-name")[0].text
                        data = resource.xpath("data")[0].text
                        resources.append({"filename": r_title, "data": data})
                    except IndexError:
                        pass
            note_dict['resources'] = resources
            created_string = parse('19700101T000017Z')
            if note.xpath('created'):
                created_string = parse(note.xpath('created')[0].text)
            updated_string = created_string
            if note.xpath('updated'):
                updated_string = parse(note.xpath('updated')[0].text)
            note_dict['createdate'] = created_string.strftime(self.date_fmt)
            note_dict['modifydate'] = updated_string.strftime(self.date_fmt)
            tags = [tag.text for tag in note.xpath('tag')]
            if self.fmt == 'csv':
                tags = " ".join(tags)
            note_dict['tags'] = tags
            note_dict['content'] = ''
            content = note.xpath('content')
            if content:
                raw_text = content[0].text
                # TODO: Option to go to just plain text, no markdown
                converted_text = self._convert_html_markdown(title, raw_text)
                if self.fmt == 'csv':
                    # XXX: DictWriter can't handle unicode. Just
                    #      ignoring the problem for now.
                    converted_text = converted_text.encode('ascii', 'ignore')
                note_dict['content'] = converted_text
            notes.append(note_dict)
        return notes

    def convert(self):
        if not os.path.exists(self.enex_filename):
            print("File does not exist: %s" % self.enex_filename)
            sys.exit(1)
        # TODO: use with here, but pyflakes barfs on it
        enex_file = io.open(self.enex_filename, encoding='utf8')
        xml_tree = self._load_xml(enex_file)
        enex_file.close()
        notes = self.prepare_notes(xml_tree)
        if self.fmt == 'csv':
            self._convert_csv(notes)
        if self.fmt == 'json':
            self._convert_json(notes)
        if self.fmt == 'dir':
            self._convert_dir(notes)

    def _convert_html_markdown(self, title, text):
        html2plain = HTML2Text(None, "")
        html2plain.feed("<h1>%s</h1>" % title)
        html2plain.feed(text)
        return html2plain.close()

    def _convert_csv(self, notes):
        if self.stdout:
            simple_file = io.StringIO()
        else:
            simple_file = io.open(self.simple_filename, 'w', encoding='utf8')
        writer = DictWriter(simple_file, self.fieldnames)
        writer.writerows(notes)
        if self.stdout:
            simple_file.seek(0)
            # XXX: this is only for the StringIO right now
            sys.stdout.write(simple_file.getvalue())
        simple_file.close()

    def _convert_json(self, notes):
        if self.simple_filename is None:
            sys.stdout.write(json.dumps(notes))
        else:
            with io.open(self.simple_filename, 'w', encoding='utf8') as output_file:
                json.dump(notes, output_file)

    def _convert_dir(self, notes):
        if self.simple_filename is None:
            sys.stdout.write(json.dumps(notes))
        else:
            if os.path.exists(self.simple_filename) and not os.path.isdir(self.simple_filename):
                print('"%s" exists but is not a directory. %s' % self.simple_filename)
                sys.exit(1)
            elif not os.path.exists(self.simple_filename):
                os.makedirs(self.simple_filename)
            for note in notes:
                # Overwrite duplicates
                # output_file_path = os.path.join(self.simple_filename, note['title'] + '.md')
                # Check for duplicates
                # Filename is truncated to 100 characters (Windows MAX_PATH is 255) - not a perfect fix but an improvement
                filename = self._format_filename(note['title'][0:99])
                output_file_path_no_ext_original = os.path.join(self.simple_filename, filename)
                output_file_path_no_ext = output_file_path_no_ext_original
                count = 0
                while os.path.isfile(output_file_path_no_ext + ".md"):
                    count = count + 1
                    output_file_path_no_ext = output_file_path_no_ext_original + " (" + str(count) + ")"
                output_file_path = output_file_path_no_ext + ".md"
                with io.open(output_file_path, 'w', encoding='utf8') as output_file:
                    if self.metadata:
                        output_file.write(self._metadata(note))
                    output_file.write(note['content'])
                for resource in note['resources']:
                    resource_output_path = os.path.join(self.simple_filename, str(count) + "-" + resource['filename'])
                    rh = open(resource_output_path, "wb")
                    rh.write(resource["data"].decode("base64"))
                    rh.close()
                with open(output_file_path, 'w') as output_file:
                    output_file.write(note['content'].encode(encoding='utf-8'))

    def _format_filename(self, s):
        for c in r'[]/\;,><&*:%=+@!#^()|?^':
            s = s.replace(c, '-')
        return s

    def _metadata(self, note):
        """
        optionally print metadata of note. Default is 'all', but can be limited
        to any combination of 'title', 'date', 'keywords'. Output is in
        MultiMarkdown format, but also rendered nicely in standard Markdown.
        """
        # Tags is a selectable option when exporting from Evernote, so we can not
        # be sure that it is available
        keywords = u", ".join(note.get('tags', []))
        
        # XXX two spaces at the end of a metadata line are intentionally set,
        # so that regular markdown renderers append a linebreak
        md = {'title': u"Title: {}  \n".format(note['title']),
              'date': u"Date: {}  \n".format(note['createdate']),
              'keywords': u"Keywords: {}  \n".format(keywords)}
        if 'all' in self.metadata:
            return u"{title}{date}{keywords}\n".format(**md)
        md_lines = map(lambda l: md[l], self.metadata)
        return u"".join(md_lines) + u"\n"
