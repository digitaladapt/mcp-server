#!/usr/bin/env bash
# hello.sh – prints a friendly greeting, optionally upper-cased.
#
# Used by registry/hello.yaml.  Supports a single positional `name`
# argument and an optional `--upper` flag.

upper=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --upper)
            upper=true
            shift
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

name="$1"

if [[ "$upper" = true ]]; then
    echo "Hello, $(echo "$name" | tr '[:lower:]' '[:upper:]')!"
else
    echo "Hello, $name!"
fi
