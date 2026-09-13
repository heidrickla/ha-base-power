#!/usr/bin/env python3
"""Decode the base64 FileDescriptorProtos embedded by @bufbuild/protobuf.

Connect-ES generated code ships each .proto as a base64 serialized
FileDescriptorProto passed to fileDesc(). Decoding them recovers the exact
API contract: package, services, RPC methods with their input/output types,
and every message field with type and number.

    python decode_descriptors.py <strings.literal.txt> <outdir>
"""
import base64
import os
import sys

from google.protobuf import descriptor_pb2

LABEL = {1: "optional", 2: "required", 3: "repeated"}
# proto field type enum -> source name
TYPE = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32",
    6: "fixed64", 7: "fixed32", 8: "bool", 9: "string", 10: "group",
    11: "message", 12: "bytes", 13: "uint32", 14: "enum", 15: "sfixed32",
    16: "sfixed64", 17: "sint32", 18: "sint64",
}


def short(type_name: str) -> str:
    return type_name.lstrip(".") if type_name else ""


def render_message(m, indent="  "):
    out = [f"{indent}message {m.name} {{"]
    for f in m.field:
        t = short(f.type_name) or TYPE.get(f.type, f"type{f.type}")
        lbl = "repeated " if f.label == 3 else ""
        out.append(f"{indent}  {lbl}{t} {f.name} = {f.number};")
    for e in m.enum_type:
        out.append(f"{indent}  enum {e.name} {{")
        for v in e.value:
            out.append(f"{indent}    {v.name} = {v.number};")
        out.append(f"{indent}  }}")
    for n in m.nested_type:
        if n.options.map_entry:
            continue
        out.extend(render_message(n, indent + "  "))
    out.append(f"{indent}}}")
    return out


def main():
    src, outdir = sys.argv[1], sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    decoded = 0
    services = []
    with open(src, encoding="latin-1") as fh:
        for line in fh:
            s = line.strip()
            if len(s) < 120:
                continue
            # The bundle stores these unpadded, so try each padding length.
            fd = None
            for pad in ("", "=", "=="):
                try:
                    raw = base64.b64decode(s + pad)
                    cand = descriptor_pb2.FileDescriptorProto()
                    cand.ParseFromString(raw)
                except Exception:
                    continue
                if cand.name.endswith(".proto"):
                    fd = cand
                    break
            if fd is None:
                continue
            decoded += 1
            lines = [f'// {fd.name}', f'package {fd.package};', ""]
            for svc in fd.service:
                lines.append(f"service {svc.name} {{")
                for meth in svc.method:
                    lines.append(f"  rpc {meth.name}({short(meth.input_type)}) "
                                 f"returns ({short(meth.output_type)});")
                    services.append((fd.package, svc.name, meth.name,
                                     short(meth.input_type), short(meth.output_type)))
                lines.append("}")
                lines.append("")
            for m in fd.message_type:
                lines.extend(render_message(m, ""))
                lines.append("")
            for e in fd.enum_type:
                lines.append(f"enum {e.name} {{")
                for v in e.value:
                    lines.append(f"  {v.name} = {v.number};")
                lines.append("}")
                lines.append("")
            safe = fd.name.replace("/", "_")
            with open(os.path.join(outdir, safe), "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

    print(f"decoded {decoded} descriptors into {outdir}\n")
    if services:
        print("=== RPC methods (Connect: POST /<package>.<Service>/<Method>) ===")
        for pkg, svc, meth, inp, outp in services:
            print(f"  /{pkg}.{svc}/{meth}")
            print(f"      in : {inp}")
            print(f"      out: {outp}")


if __name__ == "__main__":
    main()
