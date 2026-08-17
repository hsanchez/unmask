"""Classify shell commands that can destroy local or remote git state."""

from __future__ import annotations

import re
import shlex

_ALWAYS_BLOCK: dict[str, str] = {
  "push": "git push requires explicit human approval",
  "restore": "git restore discards working tree changes — use git stash to preserve them",
}

_GLOBAL_FLAGS_WITH_ARG: frozenset[str] = frozenset(
  {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--super-prefix",
  }
)

_GLOBAL_FLAGS_SOLO: frozenset[str] = frozenset(
  {
    "--no-pager",
    "--paginate",
    "-p",
    "--bare",
    "--no-replace-objects",
    "--literal-pathspecs",
    "--glob-pathspecs",
    "--noglob-pathspecs",
    "--icase-pathspecs",
    "--no-optional-locks",
  }
)

_GLOBAL_FLAG_PREFIXES: tuple[str, ...] = (
  "--git-dir=",
  "--work-tree=",
  "--namespace=",
  "--exec-path=",
  "--super-prefix=",
)

_WRAPPER_SPECS: dict[str, tuple[frozenset[str], frozenset[str], bool]] = {
  "env": (
    frozenset({"-u", "--unset"}),
    frozenset({"-i", "-0", "--ignore-environment", "--null"}),
    True,
  ),
  "command": (
    frozenset(),
    frozenset({"-p", "-V", "-v"}),
    False,
  ),
  "exec": (
    frozenset({"-a"}),
    frozenset({"-c", "-l"}),
    False,
  ),
  "sudo": (
    frozenset({"-C", "-D", "-g", "-h", "-p", "-R", "-r", "-t", "-T", "-u"}),
    frozenset({"-A", "-B", "-b", "-E", "-H", "-n", "-P", "-S"}),
    True,
  ),
  "nice": (
    frozenset({"-n"}),
    frozenset(),
    False,
  ),
  "nohup": (
    frozenset(),
    frozenset(),
    False,
  ),
}

_COMMAND_SHELLS: frozenset[str] = frozenset(
  {"bash", "dash", "fish", "ksh", "powershell", "pwsh", "sh", "zsh"}
)
_POSIX_SHELL_OPTIONS_WITH_ARGUMENT: frozenset[str] = frozenset(
  {"-o", "+o", "-O", "+O", "--init-file", "--rcfile"}
)
_POWERSHELL_OPTIONS_WITH_ARGUMENT: frozenset[str] = frozenset(
  {
    "-configurationname",
    "-custompipename",
    "-executionpolicy",
    "-inputformat",
    "-outputformat",
    "-settingsfile",
    "-windowstyle",
    "-workingdirectory",
  }
)
_MAX_WRAPPER_DEPTH = 4


def _is_git(token: str) -> bool:
  return token == "git" or token.endswith("/git")


def _uses_sudo(segment: str) -> bool:
  try:
    tokens = shlex.split(segment)
  except ValueError:
    tokens = segment.split()

  index = 0
  while index < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]):
    index += 1

  while index < len(tokens):
    executable = tokens[index]
    name = executable.rsplit("/", 1)[-1]
    if name == "sudo":
      return True
    if name not in _WRAPPER_SPECS:
      return False

    flags_with_argument, _flags_solo, allows_environment = _WRAPPER_SPECS[name]
    index += 1
    while index < len(tokens) and tokens[index].startswith("-"):
      token = tokens[index]
      base = token.split("=")[0] if "=" in token else token
      index += 1 if "=" in token or base not in flags_with_argument else 2
    if allows_environment:
      while index < len(tokens) and re.match(
        r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]
      ):
        index += 1
  return False


def _parse_git_call(segment: str) -> tuple[str, list[str]] | None:
  try:
    tokens = shlex.split(segment)
  except ValueError:
    tokens = segment.split()

  index = 0
  while index < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]):
    index += 1

  while index < len(tokens):
    wrapper_name = tokens[index].rsplit("/", 1)[-1]
    if wrapper_name not in _WRAPPER_SPECS:
      break
    flags_with_argument, _flags_solo, allows_environment = _WRAPPER_SPECS[wrapper_name]
    index += 1

    while index < len(tokens) and tokens[index].startswith("-"):
      token = tokens[index]
      base = token.split("=")[0] if "=" in token else token
      if base in flags_with_argument:
        index += 1 if "=" in token else 2
      else:
        index += 1

    if allows_environment:
      while index < len(tokens) and re.match(
        r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]
      ):
        index += 1

  if index >= len(tokens) or not _is_git(tokens[index]):
    return None
  index += 1

  while index < len(tokens):
    token = tokens[index]
    if token in _GLOBAL_FLAGS_WITH_ARG:
      index += 2
    elif token in _GLOBAL_FLAGS_SOLO or any(
      token.startswith(prefix) for prefix in _GLOBAL_FLAG_PREFIXES
    ):
      index += 1
    elif token.startswith("-"):
      index += 1
    else:
      break

  if index >= len(tokens):
    return None
  return tokens[index], tokens[index + 1 :]


def _powershell_command_argument(tokens: list[str], start: int) -> str | None:
  index = start
  while index < len(tokens):
    option = tokens[index]
    normalized_option = option.casefold()
    if normalized_option in {"-c", "-command"}:
      command_index = index + 1
      return tokens[command_index] if command_index < len(tokens) else None
    if normalized_option in {"-file", "-f"} or option == "--":
      return None
    if normalized_option in _POWERSHELL_OPTIONS_WITH_ARGUMENT:
      index += 2
      continue
    if not option.startswith("-"):
      return None
    index += 1
  return None


def _posix_shell_command_argument(
  shell_name: str, tokens: list[str], start: int
) -> str | None:
  command_options = (
    {"-c", "-C", "--command", "--init-command"}
    if shell_name == "fish"
    else {"-c"}
  )
  index = start
  while index < len(tokens):
    option = tokens[index]
    if option in command_options:
      command_index = index + 1
      return tokens[command_index] if command_index < len(tokens) else None
    if option.startswith("-") and not option.startswith("--") and "c" in option[1:]:
      command_index = index + 1
      return tokens[command_index] if command_index < len(tokens) else None
    if option == "--":
      return None

    option_base = option.split("=", 1)[0]
    if option_base in _POSIX_SHELL_OPTIONS_WITH_ARGUMENT:
      index += 1 if "=" in option else 2
      continue
    if not option.startswith("-"):
      return None
    index += 1
  return None


def _wrapped_shell_command(segment: str) -> str | None:
  try:
    tokens = shlex.split(segment)
  except ValueError:
    return None

  index = 0
  while index < len(tokens) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]):
    index += 1

  while index < len(tokens):
    name = tokens[index].rsplit("/", 1)[-1]
    if name not in _WRAPPER_SPECS:
      break
    flags_with_argument, _flags_solo, allows_environment = _WRAPPER_SPECS[name]
    index += 1
    while index < len(tokens) and tokens[index].startswith("-"):
      token = tokens[index]
      base = token.split("=")[0] if "=" in token else token
      index += 1 if "=" in token or base not in flags_with_argument else 2
    if allows_environment:
      while index < len(tokens) and re.match(
        r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[index]
      ):
        index += 1

  if index >= len(tokens):
    return None
  shell_name = tokens[index].rsplit("/", 1)[-1].casefold()
  if shell_name not in _COMMAND_SHELLS:
    return None
  if shell_name in {"powershell", "pwsh"}:
    return _powershell_command_argument(tokens, index + 1)
  return _posix_shell_command_argument(shell_name, tokens, index + 1)


def _short_flag(arguments: list[str], character: str) -> bool:
  return any(
    argument.startswith("-")
    and not argument.startswith("--")
    and character in argument
    for argument in arguments
  )


def _check_segment(segment: str, wrapper_depth: int) -> str | None:
  segment = re.sub(r"^[\s$(]+", "", segment).rstrip(")")
  if _uses_sudo(segment):
    return "sudo requires explicit human approval"
  if wrapper_depth < _MAX_WRAPPER_DEPTH:
    wrapped_command = _wrapped_shell_command(segment)
    if wrapped_command is not None:
      reason = _dangerous_command_reason(wrapped_command, wrapper_depth + 1)
      if reason:
        return reason
  parsed = _parse_git_call(segment.strip())
  if parsed is None:
    return None

  subcommand, arguments = parsed
  if subcommand in _ALWAYS_BLOCK:
    return _ALWAYS_BLOCK[subcommand]

  if subcommand == "reset" and "--hard" in arguments:
    return "git reset --hard discards committed work — use --soft or --mixed"

  if subcommand == "clean":
    if "--force" in arguments or _short_flag(arguments, "f"):
      return "git clean -f removes untracked files irreversibly"

  if subcommand == "branch":
    if "-D" in arguments or _short_flag(arguments, "D"):
      return "git branch -D force-deletes branches — requires explicit human approval"
    has_delete = (
      "--delete" in arguments
      or "-d" in arguments
      or _short_flag(arguments, "d")
    )
    has_force = "--force" in arguments or _short_flag(arguments, "f")
    if has_delete and has_force:
      return "git branch --delete --force force-deletes branches"

  if subcommand == "checkout":
    if "--force" in arguments or _short_flag(arguments, "f"):
      return "git checkout -f discards local changes — use git stash first"
    if arguments and arguments[0] == ".":
      return "git checkout . discards working tree changes"
    if "--" in arguments and arguments.index("--") < len(arguments) - 1:
      return "git checkout -- <paths> discards working tree changes"

  if subcommand == "switch":
    if (
      "--force" in arguments
      or "--discard-changes" in arguments
      or _short_flag(arguments, "f")
    ):
      return "git switch --force discards local changes — use git stash first"

  return None


def _shell_segments(command: str) -> list[str]:
  segments: list[str] = []
  segment_start = 0
  quote = ""
  escaped = False
  index = 0

  while index < len(command):
    character = command[index]
    if escaped:
      escaped = False
      index += 1
      continue
    if character == "\\" and quote != "'":
      escaped = True
      index += 1
      continue
    if quote:
      if character == quote:
        quote = ""
      index += 1
      continue
    if character in {"'", '"'}:
      quote = character
      index += 1
      continue

    separator_length = 0
    if command.startswith(("&&", "||"), index):
      separator_length = 2
    elif character in {";", "|", "\n"}:
      separator_length = 1
    if separator_length:
      segments.append(command[segment_start:index])
      index += separator_length
      segment_start = index
      continue
    index += 1

  segments.append(command[segment_start:])
  return segments


def _dangerous_command_reason(command: str, wrapper_depth: int) -> str | None:
  for segment in _shell_segments(command):
    reason = _check_segment(segment, wrapper_depth)
    if reason:
      return reason
  return None


def dangerous_command_reason(command: str) -> str | None:
  """Return the first recognized destructive-git reason in a shell command."""
  return _dangerous_command_reason(command, 0)
