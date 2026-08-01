# Local PC15 Task - Installed RLPy Evidence Only

Date: 2026-08-01
Classification: READ
Output: final chat response only

## Outcome

Collect exact, read-only evidence from PC15's installed Reallusion files that
hosted Codex can use to build the disposable FBX export helper. Do not design or
draft any Python code.

## Rules

- Use only read-only file listing, version inspection, and text-search commands.
- Do not use web search or summarize online documentation.
- Do not create, edit, move, copy, or delete files.
- Do not use Python or shell commands to bypass workspace restrictions.
- Do not open, click, control, or execute anything in iClone or Unreal.
- Do not inspect credentials, browser data, CAD data, or unrelated user files.
- Do not infer API names. Report only literal text found in installed files.
- Do not claim a file or API exists unless the tool output shows it.
- After two failed read-only searches of the same kind, stop that search and
  mark that item NOT FOUND or BLOCKED.

## Exact evidence requested

1. Report the installed iClone executable's file version and product version.
2. List installed Reallusion OpenPlugin/Python sample directories, if present.
3. Search only Reallusion installation and public template/sample directories
   for literal occurrences of:
   - `ExportFbxFile`
   - `CheckExportFbxHasLicense`
   - `EExportFbxOptions2_UnrealPreset`
   - `EExportFbxOptions_ExportRootMotion`
4. For every match, report:
   - exact full file path;
   - exact line number if available;
   - the exact matching line plus at most two adjacent lines;
   - whether the file appears to be an installed sample, generated API stub,
     documentation, log, or unknown.
5. List the exact filenames in any installed Reallusion Python API stub/module
   directory, but do not attempt to import or execute `RLPy`.

## Output format

Return only:

```text
STATUS: PASS, PARTIAL, or BLOCKED

ICLONE VERSION EVIDENCE
<verbatim tool output or NOT FOUND>

INSTALLED SAMPLE DIRECTORIES
<verbatim tool output or NOT FOUND>

LITERAL API MATCHES
<grouped exact paths and matching lines, or NOT FOUND>

RLPY STUB OR MODULE FILENAMES
<verbatim file listing or NOT FOUND>

SAFETY CONFIRMATION
- No files changed
- Nothing executed in iClone or Unreal
- No web research performed

NEXT REVIEW
- Hosted Codex must validate all evidence before using it
```

Do not add suggested code, example code, guessed enums, licensing advice, or a
general explanation. Stop immediately after the evidence report.
