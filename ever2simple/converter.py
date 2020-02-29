import json
import os
import sys
import io
from csv import DictWriter
from dateutil.parser import parse
from html2text import HTML2Text
from lxml import etree
import re
import unicodedata
import StringIO
import hashlib



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
        note_ids = {}
        for note in raw_notes:
            note_dict = {}
            title = note.xpath('title')[0].text
            note_dict['title'] = title
            # Check for duplicates
            # Filename is truncated to 100 characters (Windows MAX_PATH is 255) - not a perfect fix but an improvement

            orig_nfilename = self._format_filename(title[0:99])
            nfilename = orig_nfilename
            count = 0
            while (nfilename in note_ids):
                count = count + 1
                nfilename = orig_nfilename + "(" + str(count) + ")"
            note_dict["filename"] = nfilename
            note_ids[nfilename] = note_dict

            resources = []
            resources_by_id = {}
            resources_by_fname = {}
            for resource in note.xpath("resource"):
                mime = resource.xpath("mime")[0].text
                tag = unicode("""[{}]({})""")
                res_id_rx = re.compile("""objID="(\w+)" """)
                ext_rx = re.compile("""\w+/(\w+)""")

                try:
                    recogn = resource.xpath("recognition")[0].text
                    res_id = res_id_rx.findall(recogn)[0]
                except IndexError:
                    m = hashlib.md5()
                    m.update(resource.xpath("data")[0].text.decode("base64"))
                    res_id = m.hexdigest()
                    

                r_title = res_id + "." + ext_rx.findall(mime)[0]

                if mime.startswith("image"):
                    tag = unicode("""![]({})""")

                orig_name = None
                try:
                    orig_name = resource.xpath("resource-attributes")[0].xpath("file-name")[0].text
                except IndexError:
                    pass

                if orig_name is not None and len(orig_name) > 0:
                    rcount = 0
                    r_title = orig_name
                    while (r_title in resources_by_fname):
                        rcount = rcount + 1
                        r_title = "(" + str(rcount) + ")" + orig_name 

                rfilename = nfilename + "_" + self._format_filename(r_title)
                data = resource.xpath("data")[0].text
                res = {"filename": rfilename, "data": data, "tag": tag, "used": 0}
                resources.append(res)
                resources_by_id[res_id] = res
                resources_by_fname[r_title] = res
            note_dict['resources'] = resources

            # Use dateutil to figure out these dates
            # 20110610T182917Z
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
                for res_id, res_val in resources_by_id.iteritems():
                    if res_val["used"] == 0:
                        print "unused resource " + res_id

                for res_id, res_val in resources_by_id.iteritems():

                    reference_rx = re.compile('<en-media.+?hash="' + res_id + '"[^>]+?>')
                    replacement = res_val["tag"].format(res_val["filename"], res_val["filename"])
                    (raw_text, subs) = re.subn(reference_rx, replacement, raw_text)
                    if subs == 0:
                        print raw_text.encode("utf-8")
                        print "no subs for " + res_id + " " + res_val["filename"]

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
                output_file_path = os.path.join(self.simple_filename, note["filename"]) + ".md"
                if os.path.exists(output_file_path):
                    raise Exception("file already exists" + unicode(output_file_path))
                with io.open(output_file_path, 'w', encoding='utf8') as output_file:
                    if self.metadata:
                        output_file.write(self._metadata(note))
                    output_file.write(note['content'])
                for resource in note['resources']:
                    resource_output_path = os.path.join(self.simple_filename, resource["filename"])
                    if os.path.exists(resource_output_path):
                        raise Exception("file already exists" + unicode(resource_output_path))
                    rh = open(resource_output_path, "wb")
                    rh.write(resource["data"].decode("base64"))
                    rh.close()
                with open(output_file_path, 'w') as output_file:
                    output_file.write(note['content'].encode(encoding='utf-8'))

    def _format_filename(self, s):
        for c in r'[]/\;,><&*:%=+@!#^()|?^':
            s = s.replace(c, '-')
        for c in r' ':
            s = s.replace(c, '_')
        return unicodedata.normalize('NFKD', unicode(s)).encode('ascii','ignore')

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


