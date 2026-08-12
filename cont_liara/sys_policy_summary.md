# LIARA Sys Policy Summary

Generated from command policy DB/defaults in `db/<command>` and `services/tools/builtin/sys_command_policy.py`.

## Commands

### cat

- DB dir: `db/cat`
- whitelist_count: 6
- greylist_count: 1
- blacklist_count: 12

Whitelist sample:
- flag:-A
- flag:-b
- flag:-n
- flag:-s
- flag:-v
- path_prefix:/home/liara/workspace

Greylist sample:
- path_prefix:/tmp

Blacklist sample:
- flag:>
- flag:>>
- path_prefix:/dev
- path_prefix:/etc
- path_prefix:/home/liara/.config
- path_prefix:/home/liara/.gnupg
- path_prefix:/home/liara/.ssh
- path_prefix:/media
- path_prefix:/mnt
- path_prefix:/proc
- path_prefix:/root
- path_prefix:/sys

### cp

- DB dir: `db/cp`
- whitelist_count: 6
- greylist_count: 0
- blacklist_count: 10

Whitelist sample:
- flag:-R
- flag:-a
- flag:-f
- flag:-n
- flag:-r
- path_prefix:/home/liara

Greylist sample:
- (none)

Blacklist sample:
- path_prefix:/dev
- path_prefix:/etc
- path_prefix:/home/liara/.config
- path_prefix:/home/liara/.gnupg
- path_prefix:/home/liara/.ssh
- path_prefix:/media
- path_prefix:/mnt
- path_prefix:/proc
- path_prefix:/root
- path_prefix:/sys

### curl

- DB dir: `db/curl`
- whitelist_count: 18
- greylist_count: 4
- blacklist_count: 70

Whitelist sample:
- flag:--compressed
- flag:--head
- flag:--location
- flag:--show-error
- flag:--silent
- flag:--verbose
- flag:-I
- flag:-L
- flag:-S
- flag:-s
- flag:-v
- header_name:accept
- header_name:accept-encoding
- header_name:accept-language
- header_name:cache-control
- header_name:content-type
- header_name:user-agent
- header_name:x-request-id

Greylist sample:
- flag:--header
- flag:--max-time
- flag:-H
- flag:-m

Blacklist sample:
- flag:--abstract-unix-socket
- flag:--aws-sigv4
- flag:--cacert
- flag:--capath
- flag:--cert
- flag:--config
- flag:--connect-to
- flag:--cookie
- flag:--cookie-jar
- flag:--data
- flag:--data-ascii
- flag:--data-binary
- flag:--data-raw
- flag:--data-urlencode
- flag:--dns-servers
- flag:--form
- flag:--form-string
- flag:--haproxy-protocol
- flag:--include
- flag:--insecure
- flag:--interface
- flag:--json
- flag:--key
- flag:--max-redirs
- ... (+46 more)

### date

- DB dir: `db/date`
- whitelist_count: 15
- greylist_count: 0
- blacklist_count: 2

Whitelist sample:
- flag:+%A
- flag:+%B
- flag:+%H:%M:%S
- flag:+%I
- flag:+%Y-%m-%d
- flag:+%Y-%m-%d %H:%M:%S
- flag:+%Z
- flag:+%a
- flag:+%b
- flag:+%p
- flag:+%s
- flag:+%z
- flag:-I
- flag:-R
- flag:-u

Greylist sample:
- (none)

Blacklist sample:
- flag:-d
- flag:-s

### find

- DB dir: `db/find`
- whitelist_count: 12
- greylist_count: 2
- blacklist_count: 12

Whitelist sample:
- flag:-atime
- flag:-iname
- flag:-maxdepth
- flag:-mindepth
- flag:-mtime
- flag:-name
- flag:-newer
- flag:-size
- flag:-type
- type_arg:d
- type_arg:f
- type_arg:l

Greylist sample:
- flag:-print
- flag:-print0

Blacklist sample:
- flag:-delete
- flag:-exec
- flag:-execdir
- flag:-ls
- flag:-ok
- path:/dev
- path:/etc
- path:/media
- path:/mnt
- path:/proc
- path:/root
- path:/sys

### git

- DB dir: `db/git`
- whitelist_count: 0
- greylist_count: 0
- blacklist_count: 0

Whitelist sample:
- (none)

Greylist sample:
- (none)

Blacklist sample:
- (none)

### grep

- DB dir: `db/grep`
- whitelist_count: 7
- greylist_count: 1
- blacklist_count: 16

Whitelist sample:
- flag:-E
- flag:-F
- flag:-i
- flag:-m
- flag:-n
- flag:-w
- path_prefix:/home/liara/workspace

Greylist sample:
- path_prefix:/tmp

Blacklist sample:
- flag:--exclude
- flag:--exclude-dir
- flag:--include
- flag:-R
- flag:-f
- flag:-r
- path_prefix:/dev
- path_prefix:/etc
- path_prefix:/home/liara/.config
- path_prefix:/home/liara/.gnupg
- path_prefix:/home/liara/.ssh
- path_prefix:/media
- path_prefix:/mnt
- path_prefix:/proc
- path_prefix:/root
- path_prefix:/sys

### head

- DB dir: `db/head`
- whitelist_count: 5
- greylist_count: 1
- blacklist_count: 10

Whitelist sample:
- flag:-c
- flag:-n
- flag:-q
- flag:-v
- path_prefix:/home/liara/workspace

Greylist sample:
- path_prefix:/tmp

Blacklist sample:
- path_prefix:/dev
- path_prefix:/etc
- path_prefix:/home/liara/.config
- path_prefix:/home/liara/.gnupg
- path_prefix:/home/liara/.ssh
- path_prefix:/media
- path_prefix:/mnt
- path_prefix:/proc
- path_prefix:/root
- path_prefix:/sys

### jq

- DB dir: `db/jq`
- whitelist_count: 21
- greylist_count: 6
- blacklist_count: 10

Whitelist sample:
- flag:--ascii-output
- flag:--color-output
- flag:--compact-output
- flag:--exit-status
- flag:--join-output
- flag:--monochrome-output
- flag:--null-input
- flag:--raw-input
- flag:--raw-output
- flag:--slurp
- flag:-C
- flag:-M
- flag:-R
- flag:-a
- flag:-c
- flag:-e
- flag:-j
- flag:-n
- flag:-r
- flag:-s
- path_prefix:/home/liara/workspace

Greylist sample:
- flag:--arg
- flag:--argjson
- flag:--args
- flag:--from-file
- flag:--jsonargs
- flag:-f

Blacklist sample:
- path_prefix:/dev
- path_prefix:/etc
- path_prefix:/home/liara/.config
- path_prefix:/home/liara/.gnupg
- path_prefix:/home/liara/.ssh
- path_prefix:/media
- path_prefix:/mnt
- path_prefix:/proc
- path_prefix:/root
- path_prefix:/sys

### julia

- DB dir: `db/julia`
- whitelist_count: 7
- greylist_count: 0
- blacklist_count: 19

Whitelist sample:
- flag:--quiet
- flag:--startup-file=no
- flag:--version
- flag:-q
- flag:-v
- path_prefix:/home/liara/temp
- path_prefix:/home/liara/workspace

Greylist sample:
- (none)

Blacklist sample:
- flag:--eval
- flag:--interactive
- flag:--load
- flag:--print
- flag:--project
- flag:-E
- flag:-L
- flag:-e
- flag:-i
- path_prefix:/dev
- path_prefix:/etc
- path_prefix:/home/liara/.config
- path_prefix:/home/liara/.gnupg
- path_prefix:/home/liara/.ssh
- path_prefix:/media
- path_prefix:/mnt
- path_prefix:/proc
- path_prefix:/root
- path_prefix:/sys

### ls

- DB dir: `db/ls`
- whitelist_count: 9
- greylist_count: 2
- blacklist_count: 10

Whitelist sample:
- flag:-1
- flag:-A
- flag:-R
- flag:-S
- flag:-a
- flag:-h
- flag:-l
- flag:-t
- path_prefix:/home/liara/workspace

Greylist sample:
- flag:--color
- path_prefix:/tmp

Blacklist sample:
- path_prefix:/dev
- path_prefix:/etc
- path_prefix:/home/liara/.config
- path_prefix:/home/liara/.gnupg
- path_prefix:/home/liara/.ssh
- path_prefix:/media
- path_prefix:/mnt
- path_prefix:/proc
- path_prefix:/root
- path_prefix:/sys

### mkdir

- DB dir: `db/mkdir`
- whitelist_count: 3
- greylist_count: 0
- blacklist_count: 7

Whitelist sample:
- flag:-p
- path_prefix:/home/liara/temp
- path_prefix:/home/liara/workspace

Greylist sample:
- (none)

Blacklist sample:
- path_prefix:/dev
- path_prefix:/etc
- path_prefix:/media
- path_prefix:/mnt
- path_prefix:/proc
- path_prefix:/root
- path_prefix:/sys

### mv

- DB dir: `db/mv`
- whitelist_count: 0
- greylist_count: 0
- blacklist_count: 0

Whitelist sample:
- (none)

Greylist sample:
- (none)

Blacklist sample:
- (none)

### python

- DB dir: `db/python`
- whitelist_count: 0
- greylist_count: 0
- blacklist_count: 0

Whitelist sample:
- (none)

Greylist sample:
- (none)

Blacklist sample:
- (none)

### python3

- DB dir: `db/python3`
- whitelist_count: 5
- greylist_count: 2
- blacklist_count: 20

Whitelist sample:
- flag:-B
- flag:-OO
- flag:-c
- flag:-q
- flag:-u

Greylist sample:
- flag:-W
- flag:-m

Blacklist sample:
- call:__import__
- call:breakpoint
- call:compile(
- call:eval(
- call:exec(
- call:open(
- flag:--
- flag:-i
- import:ctypes
- import:ftplib
- import:http
- import:importlib
- import:os
- import:requests
- import:shutil
- import:smtplib
- import:socket
- import:subprocess
- import:sys
- import:urllib

### tail

- DB dir: `db/tail`
- whitelist_count: 5
- greylist_count: 1
- blacklist_count: 10

Whitelist sample:
- flag:-c
- flag:-n
- flag:-q
- flag:-v
- path_prefix:/home/liara/workspace

Greylist sample:
- path_prefix:/tmp

Blacklist sample:
- path_prefix:/dev
- path_prefix:/etc
- path_prefix:/home/liara/.config
- path_prefix:/home/liara/.gnupg
- path_prefix:/home/liara/.ssh
- path_prefix:/media
- path_prefix:/mnt
- path_prefix:/proc
- path_prefix:/root
- path_prefix:/sys

### tar

- DB dir: `db/tar`
- whitelist_count: 0
- greylist_count: 0
- blacklist_count: 0

Whitelist sample:
- (none)

Greylist sample:
- (none)

Blacklist sample:
- (none)

### tee

- DB dir: `db/tee`
- whitelist_count: 2
- greylist_count: 2
- blacklist_count: 10

Whitelist sample:
- path_prefix:/home/liara/temp
- path_prefix:/home/liara/workspace

Greylist sample:
- flag:--append
- flag:-a

Blacklist sample:
- path_prefix:/dev
- path_prefix:/etc
- path_prefix:/home/liara/.config
- path_prefix:/home/liara/.gnupg
- path_prefix:/home/liara/.ssh
- path_prefix:/media
- path_prefix:/mnt
- path_prefix:/proc
- path_prefix:/root
- path_prefix:/sys

### time

- DB dir: `db/time`
- whitelist_count: 15
- greylist_count: 0
- blacklist_count: 2

Whitelist sample:
- flag:+%A
- flag:+%B
- flag:+%H:%M:%S
- flag:+%I
- flag:+%Y-%m-%d
- flag:+%Y-%m-%d %H:%M:%S
- flag:+%Z
- flag:+%a
- flag:+%b
- flag:+%p
- flag:+%s
- flag:+%z
- flag:-I
- flag:-R
- flag:-u

Greylist sample:
- (none)

Blacklist sample:
- flag:-d
- flag:-s

### touch

- DB dir: `db/touch`
- whitelist_count: 0
- greylist_count: 0
- blacklist_count: 0

Whitelist sample:
- (none)

Greylist sample:
- (none)

Blacklist sample:
- (none)

