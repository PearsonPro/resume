import copy
import glob
import hashlib
import os
import re
import shutil
import subprocess
import time

import git
import jinja2
import yaml


with open("config.yaml") as configuration_file:
    config = yaml.load(configuration_file)
os.makedirs(config["BUILD_DIR"], exist_ok=True)
os.makedirs(os.path.join(config["OUTPUT_DIR"],
                         config["LETTERS_DIR"]), exist_ok=True)

last_updated = time.localtime(git.Repo().head.commit.committed_date)
last_updated_string = time.strftime(config["DATE_FMT"], last_updated)


def main():
    with open(os.path.join(config["YAML_DIR"],
                           config["YAML_MAIN"] + ".yaml")) as resume_data:
        data = yaml.load(resume_data, Loader)
    with open(os.path.join(config["YAML_DIR"],
                           config["YAML_STYLE"] + ".yaml")) as style_data:
        data.update(**yaml.load(style_data))
    with open(
        os.path.join(config["YAML_DIR"], config["YAML_BUSINESSES"] + ".yaml")
    ) as business_data:
        businesses = yaml.load(business_data)

    for section in data["sections"]:
        if ("type" in section
                and section["type"] == "publications"
                and "items" not in section):
            with open(
                os.path.join(config["YAML_DIR"],
                             config["YAML_PUBLICATIONS"] + ".yaml")
            ) as pub_data:
                pubs = yaml.load(pub_data)
            if not pubs:
                data["sections"].remove(section)
            else:
                section["items"] = pubs
            break

    hashes = {f: md5_hash(f)
              for f in glob.glob("{}/*.tex".format(config["BUILD_DIR"]))}

    process_resume(HTML_CONTEXT, data)
    process_resume(LATEX_CONTEXT, data)
    process_resume(MARKDOWN_CONTEXT, data)

    try:
        for business in businesses:
            data["business"] = businesses[business]
            data["business"]["body"] = LATEX_CONTEXT.render_template(
                config["LETTER_FILE_NAME"], data
            )
            process_resume(LATEX_CONTEXT, data, base=business)
    except TypeError:
        pass

    compile_latex(hashes)
    copy_to_output()


def process_resume(context, data, base=config["BASE_FILE_NAME"]):
    rendered_resume = context.render(data)
    context.write(rendered_resume, base=base)


def compile_latex(hashes):
    for input_file in glob.glob("{}/*.tex".format(config["BUILD_DIR"])):
        if (input_file in hashes and md5_hash(input_file) != hashes[input_file]
                or not os.path.exists(input_file.replace(".tex", ".pdf"))):
            subprocess.call(
                "xelatex -output-dir={} {}".format(config["BUILD_DIR"],
                                                   input_file).split()
            )


def copy_to_output():
    for ext in ("pdf", "md", "html"):
        for pdf in glob.glob("{}/*.{}".format(config["BUILD_DIR"], ext)):
            if os.path.basename(pdf).startswith("0_"):
                shutil.copy(pdf, config["OUTPUT_DIR"])
            else:
                shutil.copy(pdf, os.path.join(config["OUTPUT_DIR"],
                                              config["LETTERS_DIR"]))


def md5_hash(filename):
    with open(filename) as fin:
        return hashlib.md5(fin.read().encode()).hexdigest()


class LoaderMeta(type):
    def __new__(metacls, __name__, __bases__, __dict__):
        """Add include constructor to class."""

        # register the include constructor on the class
        cls = super().__new__(metacls, __name__, __bases__, __dict__)
        cls.add_constructor('!include', cls.construct_include)

        return cls


class Loader(yaml.Loader, metaclass=LoaderMeta):
    """YAML Loader with `!include` constructor."""
    def __init__(self, stream):
        """Initialise Loader."""

        try:
            self._root = os.path.split(stream.name)[0]
        except AttributeError:
            self._root = os.path.curdir

        super().__init__(stream)

    def construct_include(self, node):
        """Include file referenced at node."""

        filename = os.path.abspath(os.path.join(
            self._root, self.construct_scalar(node)
        ))
        extension = os.path.splitext(filename)[1].lstrip('.')

        with open(filename, 'r') as f:
            if extension in ('yaml', 'yml'):
                return yaml.load(f, Loader)
            else:
                return ''.join(f.readlines())


class ContextRenderer(object):
    def __init__(self, context_name, filetype, jinja_options, replacements):
        self.filetype = filetype
        self.replacements = replacements

        context_templates_dir = os.path.join(config["TEMPLATES_DIR"],
                                             context_name)

        self.base_template = config["BASE_FILE_NAME"]
        self.context_type_name = context_name + "type"

        self.jinja_options = jinja_options.copy()
        self.jinja_options["loader"] = jinja2.FileSystemLoader(
            searchpath=context_templates_dir
        )
        self.jinja_options["undefined"] = jinja2.StrictUndefined
        self.jinja_env = jinja2.Environment(**self.jinja_options)

    def make_replacements(self, data):
        data = copy.copy(data)

        if isinstance(data, str):
            for o, r in self.replacements:
                data = re.sub(o, r, data)

        elif isinstance(data, dict):
            for k, v in data.items():
                data[k] = self.make_replacements(v)

        elif isinstance(data, list):
            for idx, item in enumerate(data):
                data[idx] = self.make_replacements(item)

        return data

    def render_template(self, template_name, data):
        full_name = template_name + self.filetype
        return self.jinja_env.get_template(full_name).render(**data)

    @staticmethod
    def _make_double_list(items):
        double_list = [{"first": items[i * 2], "second": items[i * 2 + 1]}
                       for i in range(len(items) // 2)]
        if len(items) % 2:
            double_list.append({"first": items[-1]})
        return double_list

    # noinspection PyTypeChecker
    def render(self, data):
        data = self.make_replacements(data)
        self._name = data["name"]["abbrev"]

        body = ""
        for section_data in data["sections"]:
            section_data["theme"] = data["theme"]

            if self.context_type_name in section_data:
                section_type = section_data[self.context_type_name]
            elif "type" in section_data:
                section_type = section_data["type"]
            else:
                section_type = config["DEFAULT_SECTION"]

            if section_type == "double_items":
                section_data["items"] = self._make_double_list(
                    section_data["items"])

            section_template_name = os.path.join(
                config["SECTIONS_DIR"], section_type
            )

            rendered_section = self.render_template(
                section_template_name, section_data
            )
            body += rendered_section.rstrip() + "\n\n\n"

        data["body"] = body
        data["updated"] = last_updated_string

        return self.render_template(self.base_template, data).rstrip() + "\n"

    def write(self, output_data, base=config["BASE_FILE_NAME"]):
        if base == config["BASE_FILE_NAME"]:
            prefix = "0_"
        else:
            prefix = ""
        output_file = os.path.join(
            config["BUILD_DIR"],
            "{prefix}{name}_{base}{ext}".format(prefix=prefix,
                                                name=self._name,
                                                base=base,
                                                ext=self.filetype)
        )
        with open(output_file, "w") as fout:
            fout.write(output_data)


LATEX_CONTEXT = ContextRenderer(
    "latex",
    ".tex",
    dict(
        block_start_string='~<',
        block_end_string='>~',
        variable_start_string='<<',
        variable_end_string='>>',
        comment_start_string='<#',
        comment_end_string='#>',
        trim_blocks=True,
        lstrip_blocks=True,
    ),
    []
)


MARKDOWN_CONTEXT = ContextRenderer(
    'markdown',
    '.md',
    dict(
        trim_blocks=True,
        lstrip_blocks=True
    ),
    [
        (r'\\ ', ' '),                      # spaces
        (r'\\textbf{([^}]*)}', r'**\1**'),  # bold text
        (r'\\textit{([^}]*)}', r'*\1*'),    # italic text
        (r'\\LaTeX', 'LaTeX'),              # \LaTeX to boring old LaTeX
        (r'\\TeX', 'TeX'),                  # \TeX to boring old TeX
        ('---', '-'),                       # em dash
        ('--', '-'),                        # en dash
        (r'``([^\']*)\'\'', r'"\1"'),       # quotes
    ]
)


HTML_CONTEXT = ContextRenderer(
    'html',
    '.html',
    dict(
        trim_blocks=True,
        lstrip_blocks=True
    ),
    [
        (r'\\ ', '&nbsp;'),                              # spaces
        (r'\\textbf{([^}]*)}', r'<strong>\1</strong>'),  # bold
        (r'\\textit{([^}]*)}', r'<em>\1</em>'),          # italic
        (r'\\LaTeX', 'LaTeX'),                           # \LaTeX
        (r'\\TeX', 'TeX'),                               # \TeX
        ('---', '&mdash;'),                              # em dash
        ('--', '&ndash;'),                               # en dash
        (r'``([^\']*)\'\'', r'"\1"'),                    # quotes
    ]
)


if __name__ == '__main__':
    main()
