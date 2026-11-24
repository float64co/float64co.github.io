"""Manual pages for the simulated programs. Only implemented options are listed."""

from __future__ import annotations

# name: (summary, synopsis, description, [(option, text), ...])
PAGES: dict[str, tuple[str, str, str, list[tuple[str, str]]]] = {
    "ls": ("list directory contents", "ls [-1alA] [FILE]...", "List information about the FILEs (the current directory by default).",
           [("-a", "do not ignore entries starting with ."), ("-A", "like -a (no . and ..)"), ("-l", "use a long listing format"), ("-1", "list one file per line")]),
    "cat": ("concatenate files and print on the standard output", "cat [-n] [FILE]...", "With no FILE, read standard input.", [("-n", "number all output lines")]),
    "echo": ("display a line of text", "echo [-neE] [STRING]...", "Echo the STRING(s) to standard output.",
             [("-n", "do not output the trailing newline"), ("-e", "enable interpretation of backslash escapes")]),
    "pwd": ("print name of current/working directory", "pwd", "Print the full filename of the current working directory.", []),
    "cd": ("change the working directory (shell builtin)", "cd [DIR | -]", "Change the current directory to DIR (default: $HOME). `cd -` returns to the previous directory.", []),
    "mkdir": ("make directories", "mkdir [-p] DIRECTORY...", "Create the DIRECTORY(ies), if they do not already exist.", [("-p", "make parent directories as needed")]),
    "rm": ("remove files or directories", "rm [-rf] FILE...", "Remove each FILE. There is no undo and no trash.", [("-r, -R", "remove directories and their contents recursively"), ("-f", "ignore nonexistent files")]),
    "cp": ("copy files", "cp SOURCE DEST", "Copy SOURCE to DEST, or into DEST if it is a directory. Single files only.", []),
    "mv": ("move (rename) files", "mv SOURCE DEST", "Rename SOURCE to DEST, or move it into DEST if it is a directory.", []),
    "touch": ("create empty files", "touch FILE...", "Create each FILE if it does not exist.", []),
    "chmod": ("change file mode bits", "chmod MODE FILE...", "MODE is octal (755) or symbolic ([ugoa]*[+-][rwx]+, e.g. +x or u-w).", []),
    "head": ("output the first part of files", "head [-n N] [FILE]...", "Print the first 10 lines of each FILE.", [("-n N", "print the first N lines (also -N)")]),
    "tail": ("output the last part of files", "tail [-n N] [FILE]...", "Print the last 10 lines of each FILE.", [("-n N", "print the last N lines (also -N)")]),
    "wc": ("print newline, word, and byte counts", "wc [-lwc] [FILE]...", "Print counts for each FILE, or standard input.", [("-l", "lines"), ("-w", "words"), ("-c", "bytes")]),
    "grep": ("print lines that match patterns", "grep [-invrlc] PATTERN [FILE]...", "Search for PATTERN (a regular expression) in each FILE or standard input.",
             [("-i", "ignore case"), ("-o", "print only the matching part of each line"), ("-e PATTERN", "use PATTERN (may start with -)"), ("-E", "extended regular expressions (the default here)"), ("-h", "do not print file names"), ("-n", "prefix lines with their number"), ("-v", "select non-matching lines"), ("-r, -R", "search directories recursively"), ("-l", "print only names of matching files"), ("-c", "print only a count of matching lines")]),
    "sort": ("sort lines of text files", "sort [-rnuf] [FILE]...", "Write sorted concatenation of all FILEs to standard output.",
             [("-r", "reverse"), ("-n", "compare according to numeric value"), ("-u", "output only the first of equal lines"), ("-f", "ignore case")]),
    "uniq": ("report or omit repeated lines", "uniq [-cd] [FILE]", "Filter adjacent matching lines.", [("-c", "prefix lines by the number of occurrences"), ("-d", "only print duplicate lines")]),
    "cut": ("remove sections from each line of files", "cut -d DELIM -f LIST | -c LIST [FILE]...", "LIST is made of ranges such as 1,3 or 2-4.",
            [("-d", "use DELIM instead of TAB"), ("-f", "select only these fields"), ("-c", "select only these characters")]),
    "tr": ("translate or delete characters", "tr [-d] SET1 [SET2]", "Translate, or delete with -d, characters read from standard input. Ranges like a-z work.", [("-d", "delete characters in SET1")]),
    "tee": ("read from standard input and write to files and standard output", "tee [-a] FILE...", "Copy standard input to each FILE and to standard output.", [("-a", "append")]),
    "rev": ("reverse lines characterwise", "rev [FILE]...", "Reverse the order of characters in every line.", []),
    "seq": ("print a sequence of numbers", "seq LAST | FIRST LAST | FIRST INCREMENT LAST", "Print integers from FIRST to LAST.", []),
    "basename": ("strip directory and suffix from filenames", "basename NAME [SUFFIX]", "Print NAME with any leading directory components removed.", []),
    "dirname": ("strip last component from file name", "dirname NAME", "Print NAME with its last non-slash component removed.", []),
    "printf": ("format and print data", "printf FORMAT [ARGUMENT]...", "Supports %s %d %i %x %X %o %c %% and the escapes \\n \\t \\xHH. The format is reused while arguments remain.", []),
    "find": ("search for files in a directory hierarchy", "find [PATH...] [-name PAT] [-iname PAT] [-type f|d] [-maxdepth N] [-perm -MODE]", "Walk each PATH and print matching entries. Unreadable directories are reported. -perm -4000 finds setuid files (-2000 setgid); only this leading-minus \"all these bits\" form is supported.", []),
    "tree": ("list contents of directories in a tree-like format", "tree [-a] [DIR]", "Recursively list DIR with box-drawing characters.", [("-a", "include hidden files")]),
    "sed": ("stream editor for filtering and transforming text", "sed [-n] [-i] [-e] SCRIPT [FILE]...",
            "Commands: s/RE/REPL/[gip], d, p. Addresses: N, $, /RE/, N,M and /RE/,/RE/. Commands are separated by ;.",
            [("-n", "suppress automatic printing"), ("-i", "edit files in place"), ("-e", "add the script")]),
    "awk": ("pattern scanning and text processing (small subset)", "awk [-F SEP] '[PATTERN] {print ITEMS}' [FILE]...",
            "ITEMS may be $0 $N $NF NF NR and string literals. PATTERN may be /RE/ or a comparison like $2 > 5. END{print NR} counts lines. Variables, arithmetic and control flow are not supported.",
            [("-F", "field separator")]),
    "xxd": ("make a hex dump", "xxd [-l LEN] [-s SEEK] [FILE]", "Create a hex dump of FILE or standard input.", [("-l", "stop after LEN bytes"), ("-s", "start at SEEK bytes")]),
    "strings": ("print the sequences of printable characters in files", "strings [-n MIN] [FILE]...", "Print runs of printable characters at least MIN (default 4) long.", [("-n", "minimum length")]),
    "base64": ("base64 encode/decode data", "base64 [-d] [FILE]", "Encodes FILE or standard input, wrapped at 76 characters. -d decodes instead.", [("-d", "decode")]),
    "md5sum": ("compute MD5 checksums", "md5sum [FILE]...", "Prints a checksum and the filename for each FILE, or standard input with no arguments.", []),
    "sha1sum": ("compute SHA1 checksums", "sha1sum [FILE]...", "Prints a checksum and the filename for each FILE, or standard input with no arguments.", []),
    "sha256sum": ("compute SHA256 checksums", "sha256sum [FILE]...", "Prints a checksum and the filename for each FILE, or standard input with no arguments.", []),
    "diff": ("compare files line by line", "diff [-u] FILE1 FILE2", "Classic hunks (NcN/NaN/NdN with < and >) by default; -u gives unified @@ hunks instead. Exit status is 1 if the files differ.", [("-u", "unified output")]),
    "tar": ("an archiving utility", "tar -c|-x|-t [-z] -f ARCHIVE [-C DIR] [FILE...]",
            "-c creates ARCHIVE from FILE(s) (directories are added recursively); -x extracts it (-C sets the destination, default the current directory); "
            "-t lists its contents. -z reads/writes gzip compression (also inferred from a .tar.gz/.tgz name). Archives are real tar data, just built and read in memory.",
            [("-c", "create"), ("-x", "extract"), ("-t", "list"), ("-z", "gzip"), ("-f ARCHIVE", "archive file"), ("-C DIR", "extract into DIR")]),
    "file": ("determine file type", "file FILE...", "Report what kind of file each FILE is.", []),
    "env": ("print the environment", "env", "Print all environment variables.", []),
    "export": ("set environment variables (shell builtin)", "export NAME=VALUE...", "A leading ~ in the value, or after a colon, expands to $HOME.", []),
    "which": ("locate a command", "which COMMAND...", "Print the path of each COMMAND found in $PATH.", []),
    "type": ("describe how a name would be interpreted (shell builtin)", "type NAME...", "Report whether NAME is an alias, builtin or file.", []),
    "alias": ("define or display aliases (shell builtin)", "alias [NAME[=VALUE]]...", "With no arguments print all aliases. Aliases apply to the first word of a command.", []),
    "unalias": ("remove aliases (shell builtin)", "unalias [-a] NAME...", "Remove each NAME from the alias list.", [("-a", "remove all aliases")]),
    "history": ("show the command history (shell builtin)", "history [N | -c]", "Display numbered commands you have run.", [("-c", "clear the history")]),
    "source": ("run commands from a file in the current shell (shell builtin)", "source FILE   |   . FILE", "Read and execute commands from FILE.", []),
    "break": ("exit from a loop (shell builtin)", "break [N]", "Exit the innermost (or N-th enclosing) for/while/until loop.", []),
    "continue": ("resume the next loop iteration (shell builtin)", "continue [N]", "Resume the next iteration of the enclosing loop.", []),
    "test": ("check file types and compare values", "test EXPR   |   [ EXPR ]",
            "File tests: -e -f -d -r -w -x -s. String tests: -z -n = !=. Integer tests: -eq -ne -lt -le -gt -ge. A leading ! negates.", []),
    "bash": ("GNU Bourne-Again SHell", "bash [-c COMMAND] [FILE [ARGS]...]",
             "Scripts support variables, $(cmd), $((arith)), if/elif/else/fi, for, while, until, case/esac, { groups; }, "
             "functions (name() { ...; } or function name { ...; }) with local and return, break, continue, and positional "
             "parameters $1..$9 $# $@. Compound commands work in pipelines and accept redirects "
             "(cmd | while read l; do ...; done, done < file). Brace expansion runs before anything else: {1..5} and "
             "{5..1} are numeric ranges (optionally {1..10..2} for a step, and zero-padded if either end is, e.g. {01..5}), "
             "{a..e} is a letter range, and {x,y,z} is a plain list -- pre{a,b}post expands to preapost prebpost, and it "
             "works anywhere a word can appear, loop or not: printf '%s\\n' {1..5}. Quoting a brace ('{1..5}' or \"{1..5}\") "
             "leaves it literal. PS1 sets the prompt (\\u \\h \\w \\W \\$ \\t \\d). "
             "No arrays, [[ ]], or here-documents. Nested interactive shells are not available.", []),
    "return": ("return from a shell function (shell builtin)", "return [N]", "Stop the running function with status N (default: the last command's status).", []),
    "local": ("define function-local variables (shell builtin)", "local NAME[=VALUE]...", "Variables are restored when the function returns. Only valid inside a function.", []),
    "unset": ("remove variables or functions (shell builtin)", "unset [-f] NAME...", "Remove each variable, or each function with -f.", [("-f", "treat names as functions")]),
    "declare": ("set variables or show functions (shell builtin)", "declare [-f [NAME] | -F | NAME=VALUE...]", "-f prints function definitions and -F lists function names.", [("-f", "show function bodies"), ("-F", "list function names only")]),
    "read": ("read a line from standard input (shell builtin)", "read [-r] [NAME...]",
            "Reads one line from the piped or redirected input of the enclosing command, splits it into NAMEs (the last takes the rest; default REPLY). Returns 1 at end of input.", [("-r", "accepted; backslashes are never special")]),
    "sh": ("alias for bash in this simulation", "sh [-c COMMAND] [FILE [ARGS]...]", "See bash(1).", []),
    "vim": ("Vi IMproved, a programmer's text editor (built-in subset)", "vim [FILE]",
            "Normal mode: hjkl w b 0 ^ $ gg G x X dd yy cc dw cw yw p P o O i a I A r J D C u Ctrl-R . >> <<, with counts. "
            "Visual mode: v V, then d y c > < J o. Search: /pat ?pat n N * #. "
            "Command line: :w :q :q! :wq :x :wa :qa :e FILE :ls :bn :bp :b N :bd :N :%s/a/b/g. "
            "Options (:set NAME, :set noNAME, :set invNAME, :set NAME?): ignorecase (ic), smartcase (scs), "
            "hlsearch (hls), wrapscan (ws), number (nu). :noh clears search highlighting. "
            "Python and shell files are syntax highlighted.", []),
    "less": ("view a file a screen at a time (built-in subset)", "less [FILE]   |   COMMAND | less",
             "Reads FILE, or standard input if none is given (one of the two is required). "
             "Space/f/Ctrl-F/PgDn: forward a screen. b/Ctrl-B/PgUp: back a screen. d/Ctrl-D: forward a half screen; u/Ctrl-U: back a half screen. "
             "j/Down/Enter: forward a line. k/Up: back a line. g/Home: first line. G/End: last line. "
             "/PATTERN then Enter: search forward (a regular expression); n repeats it, N repeats it backward. q or Esc quits. "
             "If standard output is not the terminal (piped or redirected), the whole file is printed at once instead, like cat. "
             "No numeric prefix counts, backward search (?), multiple files, or follow mode (F).", []),
    "gdb": ("the GNU debugger (simulated subset)", "gdb [-q] [FILE]",
            "Opens FILE (a target binary such as ./motd) in an interactive session with Intel-syntax disassembly and no debug info. "
            "Running: run [< FILE], start, continue, stepi/si, nexti/ni, next/step (until the function returns), finish, kill, quit. "
            "Breakpoints: break/tbreak FUNC or *ADDR (a function breakpoint lands after push rbp; mov rbp,rsp), delete, disable, enable, info breakpoints. "
            "watch ADDR: stop the next time it is read back changed (checked after each instruction, not real hardware; always 8 bytes, no expressions or types). "
            "Inspecting: bt, info registers [REG], info functions [REGEX], info symbol ADDR, x/NFU ADDR (F: x d u o t c a s i; U: b h w g), "
            "print[/F] EXPR ($rip, $rsp, symbols, + - *), disassemble[/r] [FUNC]. "
            "`run` starts the program; it asks for its input line when it reaches gets(), and a crash leaves the process stopped and inspectable. "
            "Assigning to registers or memory is not simulated.", []),
    "checksec": ("show the exploit mitigations of a binary", "checksec [--file=]FILE...",
                 "Reports RELRO, stack canary, NX and PIE for each ELF FILE. With --file= the output is a one-line table; otherwise it is the pwntools-style list. "
                 "These are the protections that decide which exploit techniques work: no canary and no PIE mean a plain return-address overwrite to a known address.", []),
    "cyclic": ("generate or look up a de Bruijn pattern", "cyclic LENGTH   |   cyclic -l VALUE",
               "Print a LENGTH-byte pattern in which every 4-byte window is unique. With -l, print the offset of VALUE in the pattern; VALUE is either a number "
               "(a register's contents, little-endian: 0x6161616c) or the literal text (laaa). Feed the pattern to a program, then look up the crash address.",
               [("-l VALUE", "find the offset of VALUE")]),
    "pwn": ("pwntools command-line tools (simulated subset)", "pwn {cyclic,checksec,hex,unhex,phd} ...",
            "cyclic: see cyclic(1). checksec: see checksec(1). hex STRING: hex-encode. unhex HEX: decode to raw bytes. phd [FILE]: hexdump FILE or standard input.", []),
    "python3": ("a sandboxed Python subset", "python3 [-c CMD | FILE | -] [ARG]...",
                "Runs a script, a -c command, or standard input; with no arguments (on a terminal) starts the >>> prompt. Ctrl-D there calls "
                "exit() (bare exit/quit/help just print a hint, as in real Python); help() opens a help> sub-prompt (try \"keywords\" or "
                "\"modules\"), help(\"topic\") or help(object) gives one-shot help. "
                "Supported: numbers, str, bytes, bytearray, list, dict, set, tuple; if/for/while/try/except/finally/with, def and lambda "
                "(defaults, *args, **kwargs), comprehensions, f-strings and % formatting, slicing, unpacking, global, assert, del. "
                "Builtins: print input open len range int str bytes sorted enumerate zip map filter min max sum any all hex bin chr ord round divmod pow ... "
                "Modules: sys (argv exit stdin stdout stderr), math, struct (pack unpack calcsize), pwn (p8/p16/p32/p64 and u8..u64 with endian=, "
                "cyclic, cyclic_find, enhex, unhex, hexdump, flat, context.arch/endian, log.info, process(PATH) (a tube over the simulated program with recv, recvline, recvuntil, recvall, send, sendline, sendafter, sendlineafter, poll, close and returncode; no interactive()), "
                "ELF(PATH) (read-only: .address, .entry, .symbols/.sym, .plt, .functions, .canary/.nx/.pie/.relro, .read(addr,n), .search(bytes)), and "
                "ROP(elf) (a small chain builder limited to the pop-reg/leave/ret tail gadgets these binaries actually contain: .call(name) with no arguments for a ret2win, .raw(value), .chain(), .find_gadget([...]); "
                "a multi-argument .call(name, args) is refused, since there are no gadgets here to load the calling-convention registers). open() reads and writes the simulated filesystem only. "
                "Not available: classes, generators, decorators, other imports, str.format, attributes starting with an underscore. "
                "Scripts stop after a fixed number of steps, and huge allocations are refused.", []),
    "r2": ("radare2: static binary analysis (simulated subset)", "r2 [-A] FILE",
           "Opens FILE for analysis; the prompt shows the current seek. Commands: s ADDR (also s+N, s-N), aa/aaa (analyze), af, afl (function list), "
           "pdf [@ ADDR] (disassemble a function), pd [N], px [N], p8 [N], psz, iz (strings), ii (imports), is (symbols), iI (binary info: canary, nx, pic, relro), "
           "ie (entry), axt ADDR (cross references), ?v EXPR (flags such as sym.vuln and entry0 work; + and -), q. "
           "Chain commands with ; and filter output with ~text. Functions are only known after analysis (aa), as in the real tool. "
           "Debugging (-d) and writing are not simulated: use gdb to run the program.",
           [("-A", "analyze all at startup (like aaa)")]),
    "ps": ("report a snapshot of the current processes", "ps [aux]", "Display the (fake) process table.", []),
    "jobs": ("report background jobs (shell builtin)", "jobs", "Commands run with & execute to completion immediately and are reported once as Done. A listener (nc -l ... &) stays Running until you pick up its connection with fg or stop it with kill %N.", []),
    "fg": ("bring a job to the foreground (shell builtin)", "fg [%N]", "Only listeners can be brought forward: if something has connected to the port you opened with `nc -l -p PORT &`, fg attaches you to that connection. If nothing has connected yet, it says so and the job stays in the background.", []),
    "bg": ("resume a job in the background (shell builtin)", "bg [%N]", "Jobs are never stopped in this simulation, so there is nothing to resume.", []),
    "wait": ("wait for jobs (shell builtin)", "wait", "Returns immediately; background jobs finish synchronously (listeners keep waiting for a connection).", []),
    "kill": ("send a signal to a job (shell builtin)", "kill %N", "Stops the background listener with job number N. Real process IDs cannot be killed: only the fake processes shown by ps exist.", []),
    "nc": ("TCP connections and listeners (simulated)", "nc [-vnz] [-w SECS] HOST PORT[-PORT]   |   nc -l [-vn] [-p] PORT &",
           "Connect to a service on the simulated network and talk to it by hand: what you type is sent, replies are printed (piped input is sent all at once). "
           "-z scans ports without sending data; -v reports each success; -u uses UDP (nc -zu is how an ordinary user scans UDP, since nmap -sU needs root). With -l, nc listens on PORT (ports below 1024 need root). "
           "Run a listener in the background (&): something else can then connect to it while you work, and `fg` attaches you to the connection. Ctrl-C ends a connection. UDP listeners are not simulated.",
           [("-v", "verbose"), ("-n", "no name resolution"), ("-z", "scan for listening daemons without sending data"), ("-u", "UDP"), ("-l", "listen for an incoming connection"), ("-p PORT", "port to listen on")]),
    "ip": ("show / manipulate network devices (read-only here)", "ip addr", "Shows this machine's interfaces and addresses on the simulated network.", []),
    "ss": ("investigate sockets (simulated)", "ss [-tlnp]",
           "Lists listeners you opened with nc -l on this machine (-p adds the owning process). There is nothing else to show: outbound "
           "connections (curl, nc, ssh) finish within the one command that made them, so none are ever open to list.", [("-p", "show the owning process")]),
    "netstat": ("network statistics (simulated)", "netstat [-tlnp]", "The older equivalent of ss; same listeners, different column layout.", [("-p", "show the owning process")]),
    "ifconfig": ("configure a network interface (read-only here)", "ifconfig", "Shows this machine's interfaces and addresses, in the classic format.", []),
    "nmap": ("network scanner (simulated, in nmap's output format)", "nmap [-sn] [-Pn] [-sV] [-A] [-F] [-p PORTS|-p-] [--top-ports N] [--open] [-oN FILE] TARGET...",
             "TARGET is a host name, an IP, a range (10.10.0.1-30) or a CIDR block up to /22. The default scan covers about a thousand common ports, so a service on an unusual port needs -p-. "
             "-sV shows service versions, -A adds default scripts (page titles, robots.txt); -sn only finds live hosts; -Pn skips host discovery for machines that drop pings. "
             "Scans needing root (-sS, -sU, -O) are refused, as they would be for an ordinary user.",
             [("-sn", "ping scan: list live hosts"), ("-Pn", "treat every host as up"), ("-p-", "all 65535 ports"), ("-sV", "probe service versions"), ("-A", "versions plus default scripts"), ("-oN FILE", "save the output to FILE")]),
    "ping": ("send ICMP echo requests (simulated)", "ping [-c COUNT] HOST", "Four packets by default. Hosts that drop ICMP show 100% loss even though they may be up.", [("-c N", "number of packets")]),
    "traceroute": ("print the route packets take to a host (simulated)", "traceroute [-n] [-m MAXHOPS] HOST", "Shows the hops from the machine you are on. Destinations on a subnet your machine cannot route to report Network is unreachable.", [("-n", "no name lookups")]),
    "dig": ("DNS lookup (simulated)", "dig [@SERVER] NAME [TYPE] [+short]   |   dig -x IP   |   dig AXFR ZONE @SERVER",
            "TYPE is A, AAAA, MX, TXT, NS, CNAME, SOA, PTR or ANY. AXFR asks a name server for a whole zone; whether it answers depends on how the server is configured.",
            [("+short", "answers only"), ("-x IP", "reverse lookup")]),
    "host": ("DNS lookup, short form (simulated)", "host [-t TYPE] NAME|IP [SERVER]", "Prints addresses, mail exchangers or text records for NAME, or the name for an IP.", [("-t TYPE", "A, MX, TXT or NS")]),
    "curl": ("transfer a URL (simulated HTTP client)", "curl [options] URL...",
             "Talks to the web servers on the simulated network. Options: -i/-I headers, -v trace, -s/-S quiet, -L follow redirects, -X METHOD, -d DATA (@file reads it), -H 'Name: value', "
             "-b COOKIES|FILE, -c JARFILE, -u USER:PASS, -A AGENT, -o FILE, -O, -f, -w '%{http_code}'. "
             "https:// works against servers that have a certificate; self-signed ones fail with error 60 until you add -k. "
             "--resolve HOST:PORT:ADDR points a name at an address without DNS, which is how to ask a server for a site it hosts under another name.",
             [("-i", "include response headers"), ("-L", "follow redirects"), ("-d DATA", "POST this data"), ("-b/-c", "send / save cookies"), ("-u U:P", "HTTP basic authentication")]),
    "scp": ("copy files over ssh (simulated)", "scp [-P PORT] [-i KEYFILE] [-o StrictHostKeyChecking=no] SOURCE TARGET",
            "Exactly one of SOURCE and TARGET is remote, written user@host:path (a relative remote path starts in that user's home). Authentication, host-key questions and "
            "known_hosts work as for ssh. Single files only (-r is not simulated).", [("-P PORT", "port"), ("-i FILE", "private key file")]),
    "ssh": ("OpenSSH client (simulated)", "ssh [-p PORT] [-i KEYFILE] [-o StrictHostKeyChecking=no] [USER@]HOST [COMMAND]",
            "Logs in to a simulated machine with a password or a key file (-i). The first connection asks about the host key and remembers it in ~/.ssh/known_hosts. "
            "The shell you get belongs to that machine, and what you scan or connect to from there starts from its network position: some hosts are only reachable this way. exit returns.",
            [("-p PORT", "port"), ("-i FILE", "private key file"), ("-o StrictHostKeyChecking=no", "do not ask about unknown hosts")]),
    "date": ("print the system date and time", "date", "Print the current time.", []),
    "uname": ("print system information", "uname [-a]", "Print system information.", []),
    "id": ("print real and effective user and group IDs", "id", "Print user and group information for the current user.", []),
    "whoami": ("print effective user name", "whoami", "Print the user name associated with the current effective user ID.", []),
    "hostname": ("show the system's host name", "hostname [-I]", "Print the name of this machine, or with -I its address on the network.", [("-I", "print the network address instead of the name")]),
    "uptime": ("tell how long the system has been running", "uptime", "Print a one-line summary.", []),
    "clear": ("clear the terminal screen", "clear", "Clear the screen and scrollback. Ctrl-L does the same.", []),
    "exit": ("exit the shell (shell builtin)", "exit", "Leave the game. Progress is saved automatically.", []),
    "man": ("an interface to the manual pages", "man PAGE", "Display the manual page for PAGE.", []),
    "help": ("display help for shell builtins", "help [COMMAND]", "With no arguments, list commands. With a COMMAND, show its summary.", []),
    "true": ("do nothing, successfully", "true", "Exit with status 0.", []),
    "false": ("do nothing, unsuccessfully", "false", "Exit with status 1.", []),
    "sleep": ("delay for a specified amount of time", "sleep N", "Returns immediately in this simulation.", []),
}
for _alias, _real in (("python", "python3"), ("radare2", "r2"), ("vi", "vim"), ("nano", "vim"), ("more", "less"), ("logout", "exit"), ("ncat", "nc"), ("netcat", "nc"), ("tracepath", "traceroute"), ("nslookup", "host"), ("[", "test"), (".", "source")):
    PAGES.setdefault(_alias, PAGES[_real])


def render(name: str, width: int = 78) -> str | None:
    page = PAGES.get(name)
    if page is None:
        return None
    summary, synopsis, desc, opts = page
    title = f"{name.upper()}(1)"
    head = title + "User Commands".center(max(width - 2 * len(title), 2)) + title
    import textwrap

    def wrap(text: str) -> str:
        return "\n".join(textwrap.fill(p, width - 7, initial_indent="       ", subsequent_indent="       ") for p in text.split("\n"))

    out = [head, "", "NAME", f"       {name} - {summary}", "", "SYNOPSIS", f"       {synopsis}", "", "DESCRIPTION", wrap(desc)]
    if opts:
        out += ["", "OPTIONS"]
        for o, t in opts:
            out += [f"       {o}", textwrap.fill(t, width - 15, initial_indent="              ", subsequent_indent="              ")]
    out += ["", "NOTE", wrap("This is a simulation: only the options listed here are implemented."), ""]
    return "\n".join(out) + "\n"
