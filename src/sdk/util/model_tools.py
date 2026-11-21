import os
import inspect
import importlib
from pathlib import Path
from sdk.models.base import BaseModel

from schema import And, Or, Use, Optional, Regex
import inspect

def generate_model_documentation(models_dir: str, output_file: str = "MODEL_DOCUMENTATION.md"):
    """
    Generate documentation for all BaseModel derived classes in the models directory.
    
    :param models_dir: Path to the models directory
    :param output_file: Output markdown file path
    """
    
    models_path = Path(models_dir)
    doc_content = "# Model Documentation\n\n"
    doc_content += "This document describes all data models in the project.\n\n"
    
    # Import all modules from the models directory
    model_classes = []
    
    for py_file in sorted(models_path.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        
        module_name = py_file.stem
        try:
            # Dynamically import the module
            spec = importlib.util.spec_from_file_location(f"sdk.models.{module_name}", py_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # Find all BaseModel derived classes
            for name, obj in inspect.getmembers(module):
                if (inspect.isclass(obj) and 
                    issubclass(obj, BaseModel) and 
                    obj is not BaseModel and
                    obj.__module__ == f"sdk.models.{module_name}"):
                    model_classes.append((name, obj, py_file.name))
        except Exception as e:
            print(f"Warning: Could not import {py_file.name}: {e}")
    
    # Generate documentation for each model
    for class_name, model_class, source_file in sorted(model_classes):
        doc_content += _generate_class_documentation(class_name, model_class, source_file)
    
    # Write to file
    with open(output_file, 'w') as f:
        f.write(doc_content)
    
    print(f"Documentation generated: {output_file}")

def _generate_class_documentation(class_name: str, model_class, source_file: str) -> str:
    """Generate documentation for a single model class."""
    
    doc = f"## {class_name}\n\n"
    doc += f"**Source:** `{source_file}`\n\n"
    
    # Add class docstring if available
    if model_class.__doc__:
        doc += f"**Description:** {model_class.__doc__.strip()}\n\n"
    
    # Extract schema information
    if hasattr(model_class, 'schema') and model_class.schema:
        doc += f"### {class_name} Attributes\n\n"
        doc += "| Attribute | Type | Description |\n"
        doc += "|-----------|------|-------------|\n"
        
        schema_dict = model_class.schema.schema
        
        for attr_name, attr_schema in sorted(schema_dict.items()):
            attr_type = _extract_type_info(attr_schema)
            attr_constraint = _extract_constraints(attr_schema)
            
            constraint_text = f" {attr_constraint}" if attr_constraint else ""
            doc += f"| `{attr_name}` | {attr_type} | {constraint_text} |\n"
        
        doc += "\n"
    
    # Add default values if available
    if hasattr(model_class, '__init__'):
        try:
            # Try to instantiate with no args to see defaults
            instance = model_class()
            defaults = instance.to_dict()
            
            if defaults:
                doc += "### Default Values\n\n"
                doc += "```python\n"
                for key, value in sorted(defaults.items()):
                    if isinstance(value, str):
                        doc += f"{key}: {repr(value)}\n"
                    else:
                        doc += f"{key}: {value}\n"
                doc += "```\n\n"
        except Exception:
            pass
    
    doc += "---\n\n"
    return doc

def _collect_types(s) -> set[str]:
    """Recursive helper to collect type names from schema validators."""
    types: set[str] = set()

    # Direct types/classes
    if inspect.isclass(s) or isinstance(s, type):
        types.add(getattr(s, "__name__", str(s)))
        return types

    # Regex implies string type
    if isinstance(s, Regex):
        types.add("str")
        return types

    # Optional wraps a key, not a value; unwrap if encountered
    if isinstance(s, Optional):
        inner = getattr(s, "default", None)  # Optional(value=...) is for keys; conservatively mark unknown
        # Optional usually appears on keys; ignore for value typing
        return types

    # And/Or: inspect inner validators (support multiple attribute names across schema versions)
    if isinstance(s, (And, Or)):
        inner = (
            getattr(s, "schemas", None)
            or getattr(s, "validators", None)
            or getattr(s, "_validators", None)
            or getattr(s, "_args", None)
        )
        if inner:
            for sub in inner:
                types |= _collect_types(sub)
        return types

    # Use: get the callable (converter)
    if isinstance(s, Use):
        func = getattr(s, "func", None)
        if inspect.isclass(func) or isinstance(func, type):
            types.add(func.__name__)
        elif callable(func):
            name = getattr(func, "__name__", str(func))
            # Map common builtins
            if name in {"int", "float", "str", "bool", "list", "dict", "tuple", "set"}:
                types.add(name)
        return types

    # Sequence pattern: [type] means List[type]
    if isinstance(s, list) and len(s) == 1:
        inner_types = _collect_types(s[0])
        if inner_types:
            types.add(f"List[{', '.join(sorted(inner_types))}]")
        else:
            types.add("List[Any]")
        return types

    # Mapping pattern: {key_type: value_type} means Dict[key_type, value_type]
    if isinstance(s, dict) and len(s) == 1:
        (k, v), = s.items()
        key_types = _collect_types(k) or {"Any"}
        val_types = _collect_types(v) or {"Any"}
        types.add(f"Dict[{', '.join(sorted(key_types))}, {', '.join(sorted(val_types))}]")
        return types

    # typing annotations like List[int], Dict[str, Any]
    origin = getattr(s, "__origin__", None)
    if origin:
        types.add(getattr(origin, "__name__", str(origin)))
        return types

    # Fallback: nothing identifiable
    return types

def _extract_type_info(attr_schema) -> str:
    """Extract type information from schema validators recursively."""
    type_names = _collect_types(attr_schema)
    if not type_names:
        return "Mixed"
    # Flatten wrappers like List[...] / Dict[...] plus primitives if both appear
    # Prefer a stable, readable join
    return " | ".join(sorted(type_names))

def _extract_constraints(attr_schema) -> str:
    """Extract constraint information from schema validators (e.g., lambdas inside And/Or/Use)."""
    constraints = []

    def _collect_constraints(v):
        # Recurse into And/Or
        if isinstance(v, (And, Or)):
            inner = (
                getattr(v, "schemas", None)
                or getattr(v, "validators", None)
                or getattr(v, "_validators", None)
                or getattr(v, "_args", None)
            ) or []
            for sub in inner:
                _collect_constraints(sub)
            return

        # Use(func): show converter name
        if isinstance(v, Use):
            func = getattr(v, "func", None)
            name = getattr(func, "__name__", str(func))
            constraints.append(f"Use({name})")
            return

        # Regex pattern constraint
        if isinstance(v, Regex):
            pat = getattr(v, "regex", None) or getattr(v, "pattern", None) or str(v)
            constraints.append(f"Regex({pat})")
            return

        # Optional is for keys; skip for value constraints
        if isinstance(v, Optional):
            return

        # Skip classes/types (they’re not constraints)
        if inspect.isclass(v) or isinstance(v, type):
            return

        # Only handle Python functions/methods (not builtins/classes)
        if (inspect.isfunction(v) or inspect.ismethod(v)) and getattr(v, "__module__", "") != "builtins":
            try:
                src = inspect.getsource(v).strip()
                constraints.append(src)
            except (OSError, TypeError):
                constraints.append(getattr(v, "__name__", "predicate"))
            return

        # Container patterns: recurse
        if isinstance(v, list):
            for sub in v:
                _collect_constraints(sub)
            return
        if isinstance(v, dict):
            for k, sub in v.items():
                _collect_constraints(k)
                _collect_constraints(sub)
            return

        # Fallback: callable but not inspectable (builtins)
        if callable(v):
            constraints.append(getattr(v, "__name__", "predicate"))

    _collect_constraints(attr_schema)
    return "; ".join(constraints) if constraints else ""

if __name__ == "__main__":
    import importlib.util
    
    # Generate documentation
    models_dir = Path(__file__).resolve().parents[1] / "models"
    output_file = Path(__file__).resolve().parents[3] / "docs" / "model_documentation.md"
    
    generate_model_documentation(models_dir, output_file)
