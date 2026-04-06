git-remote-rns
==============

Reticulum remote transport for git. A mirror of this repo is hosted at `rns::6969b682832a26db5bcf2b5818e9d2f0/git-remote-rns.git`.

Installation
------------

```shell
pipx install git_remote_rns
```

Usage
-----

Start the rngit server to expose the repository
```shell
rngit --allow-all-read /path/to/repository
```

Take note of the destination hexhash that it outputs and then clone the repo.

```shell
git clone rns::<hexhash> my_repo
cd my_repo
```

If you want to push changes to the server, you can allow specific identities to push changes with `--allow-write`.

```shell
rngit --allow-write <identity> /path/to/repo
```

You can limit reading to only allow certain identities to access the repository with `--allow-read`.

```shell
rngit --allow-read <identity> /path/to/repo
```

Any identities specified in `--allow-write` will automatically have `--allow-read`.

If you don't specify any `--allow-*` flags, nobody will be able to access the server by default.

The `/path/to/repo` directory can point to a parent directory with child directories that are the repositories, in which case you can specify which repo you want to interact with when cloning.

```shell
git clone rns::<hexhash>/my_repo
```

The repositories can be bare repositories, or a worktree with the `.git` folder inside it. I don't recommend hosting the worktree versions though, as you wont be able to push updates to the checked out branch.

If you want to browse the contents of the repositories in nomadnet, you can just pass the `--nomadnet` flag to rngit.

```shell
rngit --nomadnet --allow-all-read /path/to/repositories
```

This assumes that the directory has multiple repositories in it, and without `--allow-all-read` it will expect all users to identify, and to be on the read/write list to be able to browse the repositories.
