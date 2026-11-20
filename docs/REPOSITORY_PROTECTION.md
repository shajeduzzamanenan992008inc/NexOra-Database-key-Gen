# GitHub Repository Protection — OMEGA NEX-ORA

**Stated project builder / rights holder:** Shajeduzzaman Enan. The actual GitHub account handle must be confirmed by the owner before CODEOWNERS review can be active.

This guide describes the GitHub-side controls needed to protect the **official repository**.
The README, LICENSE, and CODEOWNERS file do not technically stop edits on a user's own
computer or in a fork. GitHub permissions and branch/ruleset enforcement are required.

## 1. Confirm the owner identity

- Replace `@OWNER_GITHUB_USERNAME` in `.github/CODEOWNERS` with the owner's exact GitHub username. The supplied files do not identify the account handle, so it is intentionally not guessed.
- Confirm the account is the repository owner and has administrator access.
- Enable two-factor authentication or a passkey on the owner account.
- Keep recovery codes offline and do not share owner credentials or tokens.

## 2. Remove unauthorized write access

In **Settings → Collaborators / Manage access**:

- Keep repository Admin permission only for the owner account and any separately approved emergency owner.
- Remove unknown or unauthorized collaborators and deploy keys/tokens with write access.
- Give outside reviewers read-only access when they only need to inspect the project.
- For the strictest confidentiality, set repository visibility to **Private** and invite only approved accounts.

A public repository remains viewable and may be forked; people can alter their own copies.
These controls protect the official repository, not every possible copy.

## 3. Protect the default branch with a ruleset

Open **Settings → Rules → Rulesets → New branch ruleset** and target the default branch
(for example, `main`). Set the ruleset to **Active**, not Disabled or Evaluate. Configure: 

- Restrict updates to the owner-authorized account(s). Do not grant bypass to unapproved users, teams, or apps.
- Restrict deletions.
- Block force pushes.
- Require a pull request before merging if the project uses a reviewed PR workflow.
- Require code-owner review and dismiss stale approvals when an independent reviewer is available.
- Require approval of the most recent reviewable push when the workflow has an independent reviewer.

**Solo-owner caveat:** a pull-request review cannot be meaningfully self-approved by the PR author.
For a one-person owner-controlled repository, use the access restriction as the primary control
and choose an owner-only update/bypass model that you can operate; do not configure a required
independent approval unless a trusted second reviewer is actually available. Verify the final
ruleset behavior with a test branch before relying on it.

## 4. Enable CODEOWNERS review

- Ensure `.github/CODEOWNERS` is on the default branch.
- Replace the username placeholder with a real GitHub account that has write access.
- In the branch protection/ruleset settings, require code-owner review when the selected workflow
  supports it. CODEOWNERS requests review; it does not independently enforce a merge block.

## 5. Protect secrets and releases

- Store deployment credentials as GitHub Actions secrets or in the approved secret manager.
- Use least-privilege tokens and rotate exposed credentials immediately.
- Restrict who can publish releases and modify workflows.
- Review GitHub Actions permissions; use read-only defaults unless a workflow needs more.
- Keep production deployment approval separate from code contribution approval.

## 6. Verification checklist

- [ ] Correct owner username is configured in CODEOWNERS.
- [ ] Unauthorized write/admin collaborators have been removed.
- [ ] Owner account has MFA/passkey and secured recovery methods.
- [ ] Ruleset targets the actual default branch and is Active.
- [ ] Unauthorized accounts cannot push, merge, delete, or force-push to the protected branch.
- [ ] CODEOWNERS review behavior is tested with a harmless test pull request.
- [ ] Secret scanning and repository secret hygiene are reviewed.
- [ ] A backup/recovery path exists for repository and deployment configuration.

## GitHub documentation

- [About CODEOWNERS](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
- [Available rules for rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [Creating rulesets for a repository](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)
- [About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
