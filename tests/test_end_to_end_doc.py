"""docs/x402-nano-end-to-end.md must stay runnable and must not lie about its example.

That page exists to answer one objection: "a per-call payment requirement for
Nano that anyone can verify does not exist". It answers it with commands and
their real output, so the two ways it can rot are the two checked here:

1. A `python3` command on the page names a script or module that is not there.
   Every such command must name a file in this repository, a file the page
   itself downloads with `curl ... -o <path>`, or a standard-library module;
   and a `python3 -c` snippet may import only this repository's modules and the
   standard library.
2. The `nano:mainnet` entry quoted from the live 402 stops being a payable
   entry: it must parse as JSON and carry scheme, network, asset, payTo and an
   integer-string amount, its payTo must pass its checksum, and the amount the
   page verifies a block against must be that entry's amount - otherwise the
   page would be verifying a payment for some other price.

Nothing here reaches the network; the page's commands are not re-run.
"""

import ast
import json
import os
import re
import shlex
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import declaration
import nanoaddr

DOC = os.path.join(ROOT, "docs", "x402-nano-end-to-end.md")

FENCE = re.compile(r"^```([A-Za-z0-9_-]*)\n(.*?)^```", re.M | re.S)


def read_doc():
    with open(DOC, encoding="utf-8") as handle:
        return handle.read()


def fenced(text, *languages):
    return [body for lang, body in FENCE.findall(text) if lang in languages]


def commands(text):
    """Every `$ ` command in the console blocks, continuation lines joined.

    A command runs on while its quotes are unbalanced or its line ends in a
    backslash, which is how a multi-line `python3 -c '...'` is written.
    """
    found = []
    for body in fenced(text, "console"):
        current = None
        for line in body.splitlines():
            if current is None:
                if line.startswith("$ "):
                    current = line[2:]
                else:
                    continue
            else:
                current += "\n" + line
            if current.endswith("\\"):
                current = current[:-1]
                continue
            try:
                shlex.split(current)
            except ValueError:
                continue                    # an open quote: keep reading
            found.append(current)
            current = None
        if current is not None:
            found.append(current)           # unterminated: let the caller fail on it
    return found


def simple_commands(command):
    """Split a pipeline / && chain into its simple commands, as argv lists."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;")
    lexer.whitespace_split = True
    parts, current = [], []
    for token in lexer:
        if token and set(token) <= set("|&;"):
            if current:
                parts.append(current)
            current = []
        else:
            current.append(token)
    if current:
        parts.append(current)
    return parts


def is_stdlib(module):
    top = module.split(".")[0]
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        return top in names
    # Python < 3.10: importable and not from site-packages or this repository.
    import importlib.util
    spec = importlib.util.find_spec(top)
    if spec is None:
        return False
    origin = spec.origin or ""
    return origin in ("built-in", "frozen") or (
        "site-packages" not in origin and not origin.startswith(ROOT))


def is_repo_module(module):
    top = module.split(".")[0]
    return (os.path.isfile(os.path.join(ROOT, top + ".py"))
            or os.path.isdir(os.path.join(ROOT, top)))


def find_nano_entries(value):
    if isinstance(value, dict):
        if value.get("network") == "nano:mainnet":
            yield value
        for child in value.values():
            yield from find_nano_entries(child)
    elif isinstance(value, list):
        for child in value:
            yield from find_nano_entries(child)


class EndToEndPage(unittest.TestCase):

    def setUp(self):
        self.assertTrue(os.path.isfile(DOC), "docs/x402-nano-end-to-end.md is missing")
        self.text = read_doc()
        self.commands = commands(self.text)

    def test_the_page_carries_commands_at_all(self):
        python = [c for c in self.commands if "python3 " in c]
        self.assertGreaterEqual(len(python), 3, "expected the page's python3 commands")

    def test_every_python3_command_names_something_that_exists(self):
        downloaded = set()
        for command in self.commands:
            for argv in simple_commands(command):
                if argv and argv[0] == "curl" and "-o" in argv:
                    downloaded.add(os.path.normpath(argv[argv.index("-o") + 1]))
        checked = 0
        for command in self.commands:
            for argv in simple_commands(command):
                if not argv or argv[0] != "python3":
                    continue
                args = [a for a in argv[1:] if a not in ("-I", "-u", "-B")]
                self.assertTrue(args, "bare python3 in: %s" % command)
                checked += 1
                if args[0] == "-m":
                    module = args[1]
                    self.assertTrue(is_stdlib(module) or is_repo_module(module),
                                    "python3 -m %s: no such module here" % module)
                elif args[0] == "-c":
                    tree = ast.parse(args[1])
                    for node in ast.walk(tree):
                        names = []
                        if isinstance(node, ast.Import):
                            names = [alias.name for alias in node.names]
                        elif isinstance(node, ast.ImportFrom):
                            names = [node.module or ""]
                        for name in names:
                            self.assertTrue(is_stdlib(name) or is_repo_module(name),
                                            "python3 -c imports %r, which is neither "
                                            "the standard library nor this repo" % name)
                else:
                    script = os.path.normpath(args[0])
                    self.assertTrue(
                        os.path.isfile(os.path.join(ROOT, script)) or script in downloaded,
                        "python3 %s: not in this repository and not downloaded by "
                        "any `curl -o` on the page" % script)
        self.assertGreater(checked, 0)

    def test_every_downloaded_script_is_imported_with_its_dependency(self):
        # verify_cli.py imports nano_settlement_verify from beside itself, so a
        # page that downloads one without the other hands the reader an ImportError.
        downloaded = []
        for command in self.commands:
            for argv in simple_commands(command):
                if argv and argv[0] == "curl" and "-o" in argv:
                    downloaded.append(os.path.basename(argv[argv.index("-o") + 1]))
        if "verify_cli.py" in downloaded:
            self.assertIn("nano_settlement_verify.py", downloaded)

    def test_the_quoted_nano_entry_is_a_payable_requirement(self):
        entries = []
        for body in fenced(self.text, "json"):
            entries.extend(find_nano_entries(json.loads(body)))
        self.assertTrue(entries, "no nano:mainnet accepts[] entry in any ```json block")
        for entry in entries:
            for field in ("scheme", "network", "asset", "payTo", "amount"):
                self.assertIn(field, entry)
            self.assertEqual(entry["scheme"], "exact")
            self.assertEqual(entry["asset"], "XNO")
            self.assertIsInstance(entry["amount"], str)
            self.assertRegex(entry["amount"], r"^[1-9][0-9]*$")
            self.assertTrue(nanoaddr.is_valid(entry["payTo"]), entry["payTo"])

    def test_the_quoted_402_is_payable_by_this_repos_own_check(self):
        documents = [json.loads(body) for body in fenced(self.text, "json")]
        challenges = [d for d in documents if isinstance(d, dict) and "accepts" in d]
        self.assertTrue(challenges, "the page should quote the live 402 body")
        for doc in challenges:
            self.assertTrue(declaration.inspect(doc)["payable"], doc)

    def test_the_verified_block_is_checked_against_the_quoted_price(self):
        amounts = set()
        for body in fenced(self.text, "json"):
            for entry in find_nano_entries(json.loads(body)):
                amounts.add((entry["amount"], entry["payTo"]))
        verified = []
        for command in self.commands:
            for argv in simple_commands(command):
                if (len(argv) >= 5 and argv[0] == "python3"
                        and argv[1].endswith("verify_cli.py")):
                    verified.append((argv[3], argv[4]))
        self.assertTrue(verified, "the page should run verify_cli.py")
        # The first run is the one that settles; later runs show refusals.
        self.assertIn(verified[0], amounts,
                      "the block is verified against a price/payTo the quoted entry "
                      "does not carry")


if __name__ == "__main__":
    unittest.main()
