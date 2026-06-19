#!/usr/bin/env python3
"""
PDF Assistant — give it a PDF file and a plain-English instruction;
it uses Claude to generate and run the appropriate Python code.

Usage:
    python pdf_assistant.py [path/to/file.pdf]
"""

import sys
import os
import re
import textwrap
import traceback

import anthropic

SKILL_DIR = os.path.dirname(__file__)
MODEL = "claude-opus-4-8"

# Libraries the skill explicitly documents as installed/available
ALLOWED_LIBRARIES = ["pypdf", "pdfplumber", "reportlab", "pypdfium2"]

# Scripts available in the scripts/ directory
SCRIPTS_DIR = os.path.join(SKILL_DIR, "scripts")


def load_skill_context() -> str:
    """Load all skill knowledge files from the repo."""
    sections = []
    files = [
        ("SKILL.md", "Core PDF Skill"),
        ("forms.md", "Form Filling Workflows"),
        ("reference.md", "Advanced Reference"),
    ]
    for filename, label in files:
        path = os.path.join(SKILL_DIR, filename)
        if os.path.isfile(path):
            with open(path) as f:
                sections.append(f"=== {label} ({filename}) ===\n{f.read()}")

    # List available scripts
    if os.path.isdir(SCRIPTS_DIR):
        scripts = sorted(os.listdir(SCRIPTS_DIR))
        sections.append(
            "=== Available scripts in scripts/ ===\n"
            + "\n".join(f"  scripts/{s}" for s in scripts if s.endswith(".py"))
        )

    return "\n\n".join(sections)


def build_system_prompt(skill_context: str) -> str:
    allowed = ", ".join(ALLOWED_LIBRARIES)
    return textwrap.dedent(f"""
        You are a PDF processing assistant. The user will give you a PDF file path
        and a plain-English instruction. Your job is to write Python code that
        performs the requested operation, then return ONLY a fenced Python code
        block — nothing else, no explanation.

        STRICT RULES — you must follow all of these:
        1. Only use libraries and techniques that are explicitly documented in the
           skill files below: {allowed}.
           Do NOT use any library, module, or approach that is not shown in the
           skill documentation, even if you know it exists.
        2. You may import and call scripts/ files listed in the skill knowledge
           when they directly match the requested operation. For text edits:
           use replace_text.py for true replacements, insert_after_text.py for
           adding content under/after a matched section, and add_text_block.py
           only when coordinates are known.
        3. If the user's request cannot be fulfilled using the documented skill
           knowledge alone, do NOT attempt it. Instead return a single Python
           statement: raise NotImplementedError("This operation is not supported
           by the PDF skill.")
        4. Read the input PDF from the variable INPUT_PDF (already defined).
        5. Save output files to OUTPUT_DIR (already defined). Use descriptive names.
        6. Print a short summary of what was done. If an output file was produced,
           print its full path.
        7. Do NOT call any external API, install packages, or use shell commands
           (no subprocess, os.system, pip, etc.).

        === PDF Skill Knowledge ===
        {skill_context}
    """).strip()


def extract_code(response_text: str) -> str:
    """Pull the first ```python ... ``` block out of the Claude response."""
    match = re.search(r"```python\s*(.*?)```", response_text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: treat the whole response as code if no fences found
    return response_text.strip()


def ask_claude(pdf_path: str, instruction: str, skill_context: str) -> str:
    client = anthropic.Anthropic()

    user_message = (
        f"PDF file: {pdf_path}\n\n"
        f"Instruction: {instruction}"
    )

    print("\nAsking Claude to generate code...")
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=build_system_prompt(skill_context),
        messages=[{"role": "user", "content": user_message}],
    )

    # Get the text block (skip thinking blocks)
    for block in response.content:
        if block.type == "text":
            return block.text

    return ""


def run_code(code: str, pdf_path: str, output_dir: str):
    """Execute the generated code in a controlled namespace."""
    namespace = {
        "INPUT_PDF": pdf_path,
        "OUTPUT_DIR": output_dir,
        "__name__": "__main__",
    }
    exec(compile(code, "<generated>", "exec"), namespace)


def main():
    skill_context = load_skill_context()

    # ── Get PDF path ────────────────────────────────────────────────────────────
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        pdf_path = input("Enter the path to your PDF file: ").strip()

    pdf_path = os.path.expanduser(pdf_path)
    if not os.path.isfile(pdf_path):
        print(f"Error: file not found: {pdf_path}")
        sys.exit(1)

    print(f"PDF: {pdf_path}")

    # ── Get instruction ─────────────────────────────────────────────────────────
    print("\nWhat would you like to do with this PDF?")
    print("Examples: extract text, merge with another file, rotate page 1,")
    print("          extract tables, split into pages, add watermark, ...")
    instruction = input("\nYour instruction: ").strip()
    if not instruction:
        print("No instruction given. Exiting.")
        sys.exit(0)

    # ── Generate code ───────────────────────────────────────────────────────────
    response_text = ask_claude(pdf_path, instruction, skill_context)

    # If Claude returned a refusal instead of code, surface it clearly
    if "NotImplementedError" in response_text and "```" not in response_text:
        print("\nThis operation is not supported by the PDF skill.")
        print(response_text)
        sys.exit(1)

    code = extract_code(response_text)

    print("\n--- Generated code ---")
    print(code)
    print("--- End of code ---\n")

    confirm = input("Run this code? [Y/n]: ").strip().lower()
    if confirm in ("n", "no"):
        print("Aborted.")
        sys.exit(0)

    # ── Execute ─────────────────────────────────────────────────────────────────
    output_dir = os.path.join(
        os.path.dirname(os.path.abspath(pdf_path)),
        "pdf_assistant_output",
    )
    os.makedirs(output_dir, exist_ok=True)

    print(f"\nOutput directory: {output_dir}")
    print("\n--- Output ---")
    try:
        run_code(code, pdf_path, output_dir)
        print("--- Done ---")
    except Exception:
        print("--- Error during execution ---")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
