#!/usr/bin/env bash

# discord colors are standard 6-digit hex, but converted to a base-10 integer
# colors taken from https://xkcd.com/color/rgb/
# and built into a palette based on https://sashamaps.net/docs/resources/20-colors/
# into my color palette https://color.digitaladapt.com/
#
# DARK:   maroon(#650021), brown(#653700),   olive(#6e750e),  teal(#029386),   navy(#01153e),     black(#000000),
# BRIGHT: red(#e50000),    orange(#f97306),  yellow(#ffff14), lime(#aaff32),   green(#15b01a),
#                          cyan(#00ffff),    blue(#0343df),   purple(#7e1e9c), magenta(#c20078),  grey(#929591),
# LIGHT:  pink(#ff81c0),   apricot(#ffb16d), beige(#e6daa6),  mint(#9ffeb0),   lavender(#c79fef), white(#ffffff).

scriptRoot=$(dirname "$0")
alert=false
quiet=false
skipMsg=false
declare -A colors
colors=(
    ["maroon"]=6619169 ["brown"]=6633216    ["olive"]=7238926   ["teal"]=168838    ["navy"]=70974        ["black"]=0
    ["red"]=15007744   ["orange"]=16347910  ["yellow"]=16776980 ["lime"]=11206450  ["green"]=1421338
                       ["cyan"]=65535       ["blue"]=213983     ["purple"]=8265372 ["magenta"]=12714104  ["grey"]=9606545
    ["pink"]=16744896  ["apricot"]=16757101 ["beige"]=15129254  ["mint"]=10485424  ["lavender"]=13082607 ["white"]=16777215
)
declare -A ansiColors
ansiColors=(
    ["maroon"]="\e[37m\e[48;2;101;0;33m"      ["brown"]="\e[37m\e[48;2;101;55;0m"    ["olive"]="\e[37m\e[48;2;110;117;14m"    ["teal"]="\e[37m\e[48;2;2;147;134m"       ["navy"]="\e[37m\e[48;2;1;21;62m"     ["black"]="\e[37m\e[48;2;0;0;0m"
       ["red"]="\e[30m\e[48;2;229;0;0m"      ["orange"]="\e[30m\e[48;2;249;115;6m"  ["yellow"]="\e[30m\e[48;2;255;255;20m"    ["lime"]="\e[30m\e[48;2;170;255;50m"     ["green"]="\e[30m\e[48;2;21;176;26m"
                                               ["cyan"]="\e[30m\e[48;2;0;255;255m"    ["blue"]="\e[30m\e[48;2;3;67;223m"    ["purple"]="\e[30m\e[48;2;126;30;156m"   ["magenta"]="\e[30m\e[48;2;194;0;120m"    ["grey"]="\e[30m\e[48;2;146;149;145m"
      ["pink"]="\e[30m\e[48;2;255;129;192m" ["apricot"]="\e[30m\e[48;2;255;177;109m" ["beige"]="\e[30m\e[48;2;230;218;166m"   ["mint"]="\e[30m\e[48;2;159;254;176m" ["lavender"]="\e[30m\e[48;2;199;159;239m" ["white"]="\e[30m\e[48;2;255;255;255m"
)

print_usage() {
    echo "Usage: $0 [[-h hook-url] | -a] [-c color] [-d [distinct-name]] [-t title] [-q] (message... | -z | < file.msg)"
}

# handle all arguments provided
while getopts ':h:c:d:t:zaq' flag; do
    case "$flag" in
        h)
            if [[ "$OPTARG" == "http"*"?wait=true" ]]; then
                hookUrl="$OPTARG"
            else
                echo "Invalid hook-url provided, should be something like:"
                echo "https://discord.com/api/webhooks/***?wait=true"
                print_usage
                exit 1
            fi
            ;;
        c)
            if [[ -v "colors[$OPTARG]" ]]; then
                color="$OPTARG"
            else
                echo "Invalid color provided"
                "${scriptRoot}/colors-discord.sh"
                print_usage
                exit 1
            fi
            ;;
        d)
            # whatever the distinct key, we clean it up, since it will be used as a filename
            # replace "/" and "|" with "_"
            distinct=$(echo "$OPTARG" | sed 's/[\/\|]/_/g')
            ;;
        :)
            # '-d' without value is allowed, but generates a warning
            # in which case we'll use name of parent command with arguments
            # whatever the distinct key, we clean it up, since it will be used as a filename
            # replace "/" and "|" with "_"
            if [[ "$OPTARG" = "d" ]]; then
                distinct=$(ps -o args= $PPID | sed 's/[\/\|]/_/g')
                echo "Warning: doing distinct by parent, with key '$distinct'" >&2
            else
                echo "option '$OPTARG' provided with value"
                print_usage
                exit 1
            fi
            ;;
        t)
            # title, for supplying a title, without activing the "distinct" feature
            title="$OPTARG"
            ;;
        z)
            skipMsg=true
            ;;
        q)
            quiet=true
            ;;
        a)
            alert=true
            ;;
        *)
            echo "unknown option provided '$flag'"
            print_usage
            exit 1
            ;;
    esac
done
shift "$((OPTIND-1))"

# load in defaults from config
configFile="$scriptRoot/config.sh"
if [[ -f "$configFile" ]]; then
    source "$configFile"
else
    # when missing config.sh, hope env vars are set, and use defaults when missing
    if [[ -z "$DISCORD_SERVER_NAME" ]]; then
        DISCORD_SERVER_NAME=$(hostname)
    fi
    if [[ -z "$DISCORD_TITLE_SUFFIX" ]]; then
        DISCORD_TITLE_SUFFIX="· $DISCORD_SERVER_NAME"
    fi
    if [[ -z "$DISCORD_ALERT_HOOK" ]]; then
        DISCORD_ALERT_HOOK="$DISCORD_GENERAL_HOOK"
    fi
fi

botName="$DISCORD_SERVER_NAME"
if [[ -z "$hookUrl" ]]; then
    hookUrl="$DISCORD_GENERAL_HOOK"

    if [[ "$alert" = true ]]; then
        hookUrl="$DISCORD_ALERT_HOOK"
    fi
fi
if [[ -n "$title" ]]; then
    title="$title $DISCORD_TITLE_SUFFIX"
fi

# stop if we have no hook to send message to
if [[ -z "$hookUrl" ]]; then
    echo "Discord Hook URL must either be in config file, or supplied via '-h' argument."
    exit 1
fi

# get full message, read from arguments
message=$(echo "$@" | sed 's/^ *//' | sed 's/ *$//')

# if we have no message, read from stdin instead
if [[ -z "$message" ]] && [[ "$skipMsg" = false ]]; then
    message=$(cat - | sed 's/^ *//' | sed 's/ *$//')
fi

# sed removes blank lines from the end of the message
message=$(echo "$message" | sed -e :a -e '/^\n*$/{$d;N;ba' -e '}')
fullMsg="$message"

# output the given message, unless instructed to be quiet
if [[ "$quiet" = false ]]; then
    if [[ -n "$distinct" ]] || [[ -n "$title" ]] || [[ -n "$color" ]]; then
        if [[ -n "$color" ]]; then
            echo -e "${ansiColors[$color]} ${title-$distinct} \e[m"
        else
            # no color specified, make title bold and underlined
            echo -e "\e[1m\e[4m ${title-$distinct} \e[m"
        fi
    fi
    if [[ -n "$fullMsg" ]] && [[ "$skipMsg" = false ]]; then
        echo "$fullMsg"
    fi
fi

# if message is too long, split it, and after curl, recursively call with remainder
# 4k max, but we must account for escaping text in json, so limit to 90% capacity
extra="${message:3600}"

if [ -n "$extra" ]; then
    message="${message:0:3600}"

    # as an added elegance, when spliting into pieces, find the last \n newline,
    # and break there instead, but only if the new smaller chunk is of sufficient size
    chunk1=$(echo "$message" | head -n -1)
    chunks=$(echo "$message" | tail -n 1)
    if (( "${#chunk1}" >= 1000 )); then
        message="$chunk1"
        extra="$chunks$extra"
    fi
fi

# sed: rewrite json string, because "\u001b[m" needs to "\u001b[0m" instead
# linux is fine with the missing zero, but discord needs it
message=$(echo "$message" | jq -aRs . | sed 's/\\u001b\[m/\\u001b[0m/g')

if [[ "$message" = '"\n"' ]]; then
    if [[ "$quiet" = true ]]; then
        # quiet was specified, and there was no message, so stop now
        exit 0
    else
        # message empty, so denote as such
        message='"\ud83d\udea9 MESSAGE EMPTY \ud83d\udea9"'
    fi
else
    if [[ "$message" = *"\\u001b"* ]]; then
        prefix="ansi\n"
    fi
    # wrap message into "ansi" block, for text color support
    message="\"\`\`\`$prefix${message:1:-1}\`\`\`\""
fi

# handle distinct stuff
oldMsg=""
if [[ -n "$distinct" ]]; then
    if [[ -f "$scriptRoot/unique/$distinct.msg" ]]; then
        oldMsg=$(cat "$scriptRoot/unique/$distinct.msg")
    fi

    if [[ "$fullMsg" = "$oldMsg" ]]; then
        echo "duplicate message"
        exit 0
    fi

    echo "$fullMsg" > "$scriptRoot/unique/$distinct.msg"
fi

# build content json
content="{"
if [[ -n "$botName" ]]; then
    content="$content\"username\": $(echo "$botName" | jq -aRs .), "
fi
content="$content\"embeds\":[{\"description\": "
if [[ "$skipMsg" = false ]]; then
    content="$content$message"
else
    content="$content\"\""
fi
if [[ -n "$color" ]]; then
    content="$content, \"color\": ${colors[$color]}"
fi
if [[ -n "$distinct" ]] || [[ -n "$title" ]]; then
    content="$content, \"title\": $(echo "${title-$distinct}" | jq -aRs .)"
fi
content="$content}]}"

# actually send the message to discord
response=$(curl -s --max-time 10 --connect-timeout 5 -H "Content-Type: application/json" -X POST -d "$content" "$hookUrl" | jq -r '.message')

# response message is null unless there was an issue
if [[ "$response" != "null" ]]; then
    echo "$response" >&2
fi

# if the message was too long, send along the rest as a separate message
if [ -n "$extra" ]; then
    script=$(basename "$0")
    args=("-h" "$hookUrl")
    # when we call again, we convert distinct into a title
    if [[ -n "$distinct" ]] || [[ -n "$title" ]]; then
        args+=("-t" "${title-$distinct}")
    fi
    if [[ -n "$color" ]]; then
        args+=("-c" "$color")
    fi
    "$scriptRoot/$script" ${args[@]} "$extra"
fi
