# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from dataclasses import dataclass, field
from typing import Any, Dict, List, Type

from dataclasses_json import dataclass_json

from lisa import LisaException, schema
from lisa.transformer import Transformer


@dataclass_json()
@dataclass
class ScriptEntry:
    name: str = ""
    variables: List[str] = field(default_factory=list)
    script: str = ""


@dataclass_json()
@dataclass
class ScriptTransformerSchema(schema.Transformer):
    scripts: List[ScriptEntry] = field(default_factory=list)


class ScriptTransformer(Transformer):
    """
    It runs script on variables. below example will cover the "value" to
    True/False with the script logic.

    - name: skipped
      value: $(skipped)
      script: int(value) <= 0

    """

    @classmethod
    def type_name(cls) -> str:
        return "script"

    @classmethod
    def type_schema(cls) -> Type[schema.TypedSchema]:
        return ScriptTransformerSchema

    @property
    def _output_names(self) -> List[str]:
        runbook: ScriptTransformerSchema = self.runbook
        return [item.name for item in runbook.scripts]

    def _initialize(self, *args: Any, **kwargs: Any) -> None:
        self._index: int = 0
        self._items: List[Dict[str, Any]] = []

    def _internal_run(self) -> Dict[str, Any]:
        runbook: ScriptTransformerSchema = self.runbook
        result: Dict[str, Any] = {}
        for item in runbook.scripts:
            variables: Dict[str, Any] = {}
            for key in item.variables:
                variables[key] = self._runbook_builder.variables[key].data

            try:
                eval_result = eval(item.script, variables.copy())
            except Exception as identifier:
                raise LisaException(
                    f"'{item.script}' failed, variables: {variables}. {identifier}"
                )
            result[item.name] = eval_result
            self._log.debug(
                f"script: '{item.script}', variables: {variables}, "
                f"result: {eval_result}"
            )
        return result
