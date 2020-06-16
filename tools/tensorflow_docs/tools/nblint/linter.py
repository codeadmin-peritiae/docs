# Lint as: python3
# Copyright 2020 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
r"""Import lint tests, run lints, and report status.

A `Linter` instance imports lint tests from a style file into a structured queue
that are then run on notebook files. Depending on condition and scope options,
these are executed for the entire notebook or for each text and code cell.

A `LinterStatus` instance is returned when lint tests are run on a file. While
a single `Linter` instance can be run on multiple files, a `LinterStatus` is
associated with a single notebook file. It maintains the pass/fail state for
each lint test on the file. Additionally, `LinterStatus` implements the
formatting required to print the report that the console.
"""

import json
import sys
import textwrap
import typing

from tensorflow_docs.tools.nblint import decorator


class Linter:
  """Manages the collection of lints to execute on a notebook.

  Lint assertions are imported by style modules and dispatched by condition and
  scope. A Linter can be run on multiple notebooks.

  Attributes:
    verbose: Boolean to print more details to console. Default is False.
  """

  def __init__(self, verbose=False):
    self.verbose = verbose

  def _load_notebook(self, path):
    """Load and parse JSON data from a notebook file.

    Args:
      path: A `pathlib.Path` of a Jupyter notebook.

    Returns:
      Dict: Contains data of the parsed JSON notebook.
      String: The entire JSON source code of the notebook.
    """
    source = path.read_text(encoding="utf-8")
    try:
      data = json.loads(source)
      if not isinstance(data.get("cells"), list):
        print(
            "Error: Invalid notebook, unable to find list of cells.",
            file=sys.stderr)
        sys.exit(1)
    except (json.JSONDecodeError, ValueError) as err:
      print(
          textwrap.dedent(f"""
        Unable to load JSON for {path}:
        {err.__class__.__name__}: {err}
        """),
          file=sys.stderr)
      data = None

    return data, source

  def _run_lint_group(self, lint, data, status, path):
    """Run lint over all cells with scope and return cumulative pass/fail.

    Args:
      lint: `decorator.Lint` containg the assertion, scope, and condition.
      data: `dict` containing data of entire parse notebook.
      status: The `LinterStatus` to add individual entries for group members.
      path: `pathlib.Path` of notebook to pass to @lint defined callback.

    Returns:
      Boolean: True if lint passes for all/any cells, otherwise False.

    Raises:
      Exception: Unsupported lint condition in `decorator.Options.Cond`.
    """
    scope = lint.scope
    is_success_list = []  # Collect for each (scoped) cell in notebook.

    for cell_idx, cell in enumerate(data.get("cells")):
      # Evict notebook cells outside of scope.
      cell_type = cell.get("cell_type")
      if scope is decorator.Options.Scope.TEXT and cell_type != "markdown":
        continue
      elif scope is decorator.Options.Scope.CODE and cell_type != "code":
        continue

      # Execute lint on cell and collect result.
      source = "".join(cell["source"])
      is_success = lint.run(source, cell, path)
      is_success_list.append(is_success)

      # All lint runs get a status entry. Group success is a separate entry.
      name = f"{lint.name}__cell_{cell_idx}"
      status.add_entry(
          lint, is_success, name=name, group=lint.name, is_group_entry=True)

    # Return True/False success for entire cell group.
    if lint.cond is decorator.Options.Cond.ANY:
      return any(is_success_list)
    elif lint.cond is decorator.Options.Cond.ALL:
      return all(is_success_list)
    else:
      raise Exception("Unsupported lint condition.")

  def run(self, path, lint_dict):
    """Multiple hooks provided to run tests at specific points.

    Args:
      path: `pathlib.Path` of notebook to run lints against.
      lint_dict: A dictionary containing the lint styles.

    Returns:
      LinterStatus: Provides status and reporting of lint tests for a notebook.
    """
    data, source = self._load_notebook(path)
    if not data:
      return False

    status = LinterStatus(path, verbose=self.verbose)

    # File-level scope.
    # Lint run once for the file.
    for lint in lint_dict[decorator.Options.Scope.FILE][
        decorator.Options.Cond.ANY]:
      is_success = lint.run(source, data, path)
      status.add_entry(lint, is_success)

    # Cell-level scope.
    # These lints run on each cell, then return a cumulative result.
    for scope in [
        decorator.Options.Scope.CELLS, decorator.Options.Scope.CODE,
        decorator.Options.Scope.TEXT
    ]:
      for cond in decorator.Options.Cond:
        lints = lint_dict[scope][cond]
        for lint in lints:
          is_success = self._run_lint_group(lint, data, status, path)
          status.add_entry(lint, is_success, group=lint.name)

    return status


class LinterStatus:
  """Provides status and reporting of lint tests for a notebook.

  A new `LinterStatus` object is returned when `Linter.run` is executed on a
  given notebook. A `LinterStatus` object represents a run of all lints for a
  single notebook file. Multiple notebook files require multiple `LinterStatus`
  objects. Though multiple status objects can be created by the same `Linter`.

  The `LinterStatus` instance manages `LintStatusEntry` objects. These are added
  in the `Linter.run` for each lint test. Some entries may be a part of a larger
  lint group that represents a collective pass/fail status.

  A `LinterStatus` instance is also reponsible for printing status reports for
  entries to the console to display to the user.

  Attributes:
    path: `pathlib.Path` of notebook that lints were run against.
    verbose: Boolean to print more details to console. Default is False.
    is_success: Boolean status of entire lint report: True if all tests pass,
      otherwise False.
  """

  class LintStatusEntry(typing.NamedTuple):
    """Represents the status of a lint tested against a single section.

    Depending on the scope of the lint, one lint can create multiple
    `LintStatusEntry` objects. For example, if tested against all notebook
    cells, one status entry would be created for each cell it is run on. This
    would also create a group entry representing the cumulative conditional
    test: any/all.

    Groups are determined by a shared a group name. If an entry is designed with
    True for `is_group_entry`, that means it's a member (child) of the group.
    The cumulative status is the one member of the group that is set to False
    for `is_group_entry`.

    Attributes:
      lint: `decorator.Lint` associated with this status.
      is_success: Boolean
      name: Optional name of the status entry for reports. Default to lint name.
      group: Optional string of shared group name for multiple entries.
      is_group_entry: Boolean. If in group, True if entry is memmber/child of
        group, and Falsw if it represents the collective status of a group.
    """
    lint: decorator.Lint
    is_success: bool
    name: str
    group: typing.Optional[str]
    is_group_entry: bool

  def __init__(self, path, verbose=False):
    self.path = path
    self.verbose = verbose
    self._status_list = []  # Contains all status entries.

  def add_entry(self,
                lint,
                is_success,
                name=None,
                group=None,
                is_group_entry=False):
    """Add a new `LintStatusEntry` to report.

    Args:
      lint: `decorator.Lint` associated with this status.
      is_success: Boolean
      name: Optional name of the status entry for reports. Default to lint name.
      group: Optional string of shared group name for multiple entries.
      is_group_entry: Boolean. If in group, True if entry is memmber/child of
        group, and Falsw if it represents the collective status of a group.
    """
    if not isinstance(is_success, bool):
      raise TypeError(f"Lint status must return Boolean, got: {is_success}")
    name = name if name else lint.name
    entry = self.LintStatusEntry(lint, is_success, name, group, is_group_entry)
    self._status_list.append(entry)

  @property
  def is_success(self):
    """Represents the status of entire lint report.

    Returns:
      Boolean: True if all top-level status entries pass, otherwise False.
    """
    status = True
    for entry in self._status_list:
      if not entry.is_group_entry and not entry.is_success:
        status = False
        break
    return status

  def _format_status(self, entry):
    """Pretty-print an entry status for console (with color).

    Args:
      entry: `LintStatusEntry` with status.

    Returns:
      String: 'Pass' or 'Fail' with terminal color codes.
    """
    if entry.is_success:
      msg = "\033[32mPass\033[00m"  # Green
    else:
      if entry.is_group_entry:
        msg = "\033[33mFail\033[00m"  # Yellow: group entry
      else:
        msg = "\033[91mFail\033[00m"  # Light red: root entry
    return msg

  def __str__(self):
    """Print the entire status report of all entries to console.

    Arrange and format entries for reporting to console. If
    `LinterStatus.verbose` is True, display group member entries in addition to
    the cumulative group status. Called as: `print(linter_status)`.

    Returns:
      String containing the entire lint report.
    """
    # Sort group entries to display nested underneath parent.
    groups = {}
    # Can skip if not displaying.
    if self.verbose:
      for entry in self._status_list:
        if entry.is_group_entry:
          if entry.group in groups:
            groups[entry.group].append(entry)
          else:
            groups[entry.group] = [entry]

    # Filter top-level entries.
    root_entries = [obj for obj in self._status_list if not obj.is_group_entry]
    output = ""

    for entry in root_entries:
      # Print top-level entry.
      status = self._format_status(entry)
      name = f"{entry.lint.style}::{entry.name}"
      msg = f" | {entry.lint.message}" if entry.lint.message else ""
      output += f"{status} | {name}{msg}\n"

      # Print child entries, if applicable.
      if self.verbose and entry.group in groups:
        output += "[All results]\n"
        for child in groups[entry.group]:
          output += f"- {self._format_status(child)} | {child.name}\n"

        output += "\n"

    return output
