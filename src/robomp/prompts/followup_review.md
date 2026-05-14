# PR review on {{repo.full_name}}#{{pr.number}}

A review comment was posted on the PR you opened.

## Comment by @{{comment.author}} on `{{comment.path}}`{{comment.line_range}}

{{comment.body}}

---

Read the diff context around the cited line range, address the comment, and
push a follow-up commit on `{{workspace.branch}}`. Reply with a single
`gh_post_comment` summarizing what changed (one line per concrete fix).

If the reviewer is asking for clarification rather than a change, answer with
`gh_post_comment` and do not touch the code.
