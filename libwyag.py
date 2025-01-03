# file: libwyag.py
# author: Yug Patel
# last modified: 23 December 2024

import re
import os
import sys
import zlib
import hashlib
import argparse
import collections
import configparser
import grp, pwd
from math import ceil
from fnmatch import fnmatch
from datetime import datetime


argparser = argparse.ArgumentParser(description="The stupidest content tracker")
argsubparsers = argparser.add_subparsers(title="Commands", dest="command")
argsubparsers.required = True


def main(argv=sys.argv[1:]):
    args = argparser.parse_args(argv)
    match args.command:
        case "add":
            cmd_add(args)
        case "cat-file":
            cmd_cat_file(args)
        case "check-ignore":
            cmd_check_ignore(args)
        case "checkout":
            cmd_checkout(args)
        case "commit":
            cmd_commit(args)
        case "hash-object":
            cmd_hash_object(args)
        case "init":
            cmd_init(args)
        case "log":
            cmd_log(args)
        case "ls-files":
            cmd_ls_files(args)
        case "ls-tree":
            cmd_ls_tree(args)
        case "rev-parse":
            cmd_rev_parse(args)
        case "rm":
            cmd_rm(args)
        case "show-ref":
            cmd_show_ref(args)
        case "status":
            cmd_status(args)
        case "tag":
            cmd_tag(args)
        case _:
            print("Bad command.")


class GitRepository(object):
    """A git repository"""

    worktree = None
    gitdir = None
    conf = None

    def __init__(self, path, force=False):
        self.worktree = path
        self.gitdir = os.path.join(path, ".git")

        if not (force or os.path.isdir(self.gitdir)):
            raise Exception("Not a Git repository %s" % path)

        # Read configuration file in .git/config
        self.conf = configparser.ConfigParser()
        cf = repo_file(self, "config")

        if cf and os.path.exists(cf):
            self.conf.read([cf])
        elif not force:
            raise Exception("Configuration file missing")

        if not force:
            vers = int(self.conf.get("core", "repositoryformatversion"))
            if vers != 0:
                raise Exception("Unsupported repositoryformatversion %s" % vers)


def repo_path(repo, *path):
    """Compute path under repo's gitdir."""
    # print("In repo path function")
    # print("MERGING: ", repo, "AND ", *path)
    return os.path.join(repo.gitdir, *path)


def repo_file(repo, *path, mkdir=False):
    """Same as repo_path, but create dirname(*path) if absent.
    For eg, repo_file(r, \"refs\", \"remotes\", \"origin\", \"HEAD\")
    will create .git/refs/remotes/origin.
    """
    if repo_dir(repo, *path[:-1], mkdir=mkdir):
        return repo_path(repo, *path)


def repo_dir(repo, *path, mkdir=False):
    """Same as repo_path, but mkdir *path if path absent if mkdir."""
    path = repo_path(repo, *path)

    if os.path.exists(path):
        if os.path.isdir(path):
            return path
        else:
            raise Exception("Not a directory %s" % path)
    if mkdir:
        os.makedirs(path)
        return path
    else:
        return None


def repo_create(path):
    """Create a new repository at path."""

    repo = GitRepository(path=path, force=True)
    # First we make sure the path either doesn't exist or is an empty dir

    if os.path.exists(repo.worktree):
        if not os.path.isdir(repo.worktree):
            raise Exception(f"{path} is not a directory")
        if os.path.exists(repo.gitdir) and os.listdir(repo.gitdir):
            raise Exception(f"{path} is not empty!")
    else:
        os.makedirs(repo.worktree)

    assert repo_dir(repo, "branches", mkdir=True)
    assert repo_dir(repo, "objects", mkdir=True)
    assert repo_dir(repo, "refs", "tags", mkdir=True)
    assert repo_dir(repo, "refs", "heads", mkdir=True)

    # .git/description
    with open(repo_file(repo, "description"), "w") as f:
        f.write(
            "Unnamed repository; edit this file 'description' to name the repository.\n"
        )

    # .git/HEAD
    with open(repo_file(repo, "HEAD"), "w") as f:
        f.write("ref: refs/heads/master\n")

    with open(repo_file(repo, "config"), "w") as f:
        config = repo_default_config()
        config.write(f)

    return repo


def repo_default_config():
    ret = configparser.ConfigParser()
    ret.add_section("core")
    ret.set("core", "repositoryformatversion", "0")
    ret.set("core", "filemode", "false")
    ret.set("core", "bare", "false")

    return ret


argsp = argsubparsers.add_parser("init", help="Initialize a new, empty repository.")
argsp.add_argument(
    "path",
    metavar="directory",
    nargs="?",
    default=".",
    help="Where to create the repository.",
)


def cmd_init(args):
    repo_create(args.path)


def repo_find(path=".", required=True):
    path = os.path.realpath(path)

    if os.path.isdir(os.path.join(path, ".git")):
        return GitRepository(path)

    # If we haven't returned, recurse in parent if w
    parent = os.path.realpath(os.path.join(path, ".."))

    if parent == path:
        # Base case
        # os.path.join("/","..") == "/"
        # If parent==path, then path is root dir
        if required:
            raise Exception("No git directory.")
        else:
            return None
    # Recursive Case
    return repo_find(parent, required)


class GitObject(object):
    def __init__(self, data=None):
        if data != None:
            self.deserialize(data)
        else:
            self.init()

    def serialize(self, repo):
        """This function MUST be implemented by subclasses.
        It must read the object's contents from self.data, a byte string,
        and do whatever it takes to convert it into a meaningful representation.
        What that means, depends on each subclass."""

        raise Exception("Unimplemented!")

    def deserialize(self, data):
        raise Exception("Unimplemented!")

    def init(self):
        pass  # Just do nothing. This is a reasonable default


def object_read(repo, sha):
    """Read object sha from Git repository repo.
    Return a GitObject whose exact type depends on the object."""

    path = repo_file(repo, "objects", sha[0:2], sha[2:])
    if not os.path.isfile(path):
        return None  # Object not found

    with open(path, "rb") as f:
        raw = zlib.decompress(f.read())
        # Read object type
        x = raw.find(b" ")  # index of space
        object_type = raw[0:x]

        # Read and validate object size
        y = raw.find(b"\x00", x)  # index of null character
        # size exists b/w space and null char, size is number of chars after null chars
        # which is the real size of the file
        size = int(raw[x:y].decode("ascii"))
        if size != len(raw) - y - 1:
            raise Exception("Malformed object {0}: bad length".format(sha))

        # Pick constructor
        match object_type:
            case b"commit":
                c = GitCommit
            case b"tree":
                c = GitTree
            case b"tag":
                c = GitTag
            case b"blob":
                c = GitBlob
            case _:
                raise Exception(
                    "Unknown type {0} for object {1}".format(
                        object_type.decode("ascii"), sha
                    )
                )
        # Call constructor and return object
        return c(raw[y + 1 :])


def object_write(obj, repo=None):
    # Serialize object data
    data = obj.serialize()
    # Add header
    result = obj.object_type + b" " + str(len(data)).encode() + b"\x00" + data
    # Compute hash
    sha = hashlib.sha1(result).hexdigest()

    if repo:
        # Compute path
        path = repo_file(repo, "objects", sha[0:2], mkdir=True)

        if not os.path.exists(path):
            with open(path, "wb") as f:
                # Compress and write
                f.write(zlib.compress(result))
    return sha


class GitBlob(GitObject):
    object_type = b"blob"

    def serialize(self):
        return self.blobdata

    def serialize(self, data):
        self.blobdata = data


argsp = argsubparsers.add_parser(
    "cat-file", help="Provide content of repository objects."
)
argsp.add_argument(
    "type",
    metavar="type",
    choices=["blob", "commit", "tag", "tree"],
    help="Specify the type of object",
)
argsp.add_argument("object", metavar="object", help="The object to display")


def cmd_cat_file(args):
    repo = repo_find()
    cat_file(repo, args.object, object_type=args.type.encode())


def cat_file(repo, obj, object_type=None):
    obj = object_read(repo, object_find(repo, obj, object_type=object_type))
    sys.stdout.buffer.write(obj.serialize())


def object_find(repo, name, object_type=None, follow=True):
    return name


argsp = argsubparsers.add_parser(
    "hash-object", help="Compute object ID and optionally creates a blob from a file"
)
argsp.add_argument(
    "-t",
    metavar="type",
    dest="type",
    choices=["blob", "commit", "tag", "tree"],
    default="blob",
    help="Specify the object type",
)
argsp.add_argument(
    "-w",
    dest="write",
    action="store_true",
    help="Actually write the object into the database",
)
argsp.add_argument("path", help="Read object from <file>")


def cmd_hash_object(args):
    if args.write:
        repo = repo.find()
    else:
        repo = None

    with open(args.path, "rb") as fd:
        sha = object_hash(fd, args.type.encode(), repo)
        print(sha)


def object_hash(fd, object_type, repo=None):
    """Hash objec, writing it to repo of provided."""
    data = fd.read()
    # Choose a constructor according to object_type argument
    match object_type:
        case b"commit":
            obj = GitCommit(data)
        case b"tree":
            obj = GitTree(data)
        case b"tag":
            obj = GitTag(data)
        case b"blob":
            obj = GitBlob(data)
        case _:
            raise Exception(f"Unknown type {object_type}")
    return object_write(obj, repo)


def kvlm_parse(raw, start=0, dict=None):
    if not dict:
        dict = collections.OrderedDict()
        # We CANNOT declare the argument as dict = OrderedDict() or all
        # call to the functions will endlessly grow the same dict

    # This function is recursive: it reads a key/value pair, then call
    # itself back with the new position. So we first need to know
    # where we are: at a keyword, or already in the messageQ

    # We search for the next space and the next newLine.
    space = raw.find(b" ", start)
    endline = raw.find(b"\n", start)

    # If space appears befoe newline, we have a keyword. Otherwise,
    # it's the final message, which we just read to the end of the file.

    # Base case
    # If newline appears first (or there's no space at all, in which
    # case find returns -1), we assume a blank line. A blank line
    # means the remainder of the data is the message. We store it in the
    # dictionary, with None as the key, and return.

    if (space < 0) or (endline < space):
        assert endline == start
        dict[None] = raw[start + 1 :]
        return dict

    # Recursive case
    # we read a key-value pair and recurse for the next
    key = raw[start:space]

    # Find the end of the value. Continuation lines begin with a
    # space, so we loop until we find a "\n" not followed by a space

    end = start
    while True:
        end = raw.find(b"\n", end + 1)
        if raw[end + 1] != ord(" "):
            break

    # Grab the value
    # Also, drop the leading space on continuation lines
    value = raw[space + 1 : end].replace(b"\n ", b"\n")

    # Don't overwrite existing data contents
    if key in dict:
        if type(dict[key]) == list:
            dict[key].append(value)
        else:
            dict[key] = [dict[key], value]
    else:
        dict[key] = value

    return kvlm_parse(raw, start=end + 1, dict=dict)


def kvlm_serialize(kvlm):
    ret = b""

    # Output fields
    for k in kvlm.keys():
        # Skip the message itself
        if k == None:
            continue
        val = kvlm[k]
        # Normalize to a list
        if type(val) != list:
            val = [val]

        for v in val:
            ret += k + b" " + (v.replace(b"\n", b"\n ")) + b"\n"

    # Append message
    ret += b"\n" + kvlm[None] + b"\n"

    return ret


class GitCommit(GitObject):
    object_type = b"commit"

    def serialize(self, data):
        self.kvlm = kvlm_parse(data)

    def serialize(self):
        return kvlm_serialize(self.kvlm)

    def init(self):
        self.kvlm = dict()


argsp = argsubparsers.add_parser("log", help="Display history of a given commit.")
argsp.add_argument("commit", default="HEAD", nargs="?", help="Commit to start at.")


def cmd_log(args):
    repo = repo_find()

    print("digraph wyaglog{")
    print("  node[shape=rect]")
    log_graphviz(repo, object_find(repo, args.commit), set())
    print("}")


def log_graphviz(repo, sha, seen):
    if sha in seen:
        return
    seen.add(sha)

    commit = object_read(repo, sha)
    short_hash = sha[0:8]
    message = commit.kvlm[None].decode("utf8").strip()
    message = message.replace("\\", "\\\\")
    message = message.replace('"', '\\"')

    if "\n" in message:  # Keep only the first line
        message = message[: message.index("\n")]

    print(" c_{0} [label='{1}: {2}\"]".format(sha, sha[0:7], message))
    assert commit.object_type == b"commit"

    if not b"parent" in commit.kvlm.keys():
        # Base case: the initial commit
        return

    parents = commit.kvlm[b"parent"]

    if type(parents) != list:
        parents = [parents]

    for p in parents:
        p = p.decode("ascii")
        print(" c_{0} -> c_{1};".format(sha, p))
        log_graphviz(repo, p, seen)


if __name__ == "__main__":
    repo = GitRepository
