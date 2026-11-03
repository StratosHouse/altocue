# Money, uploads and provenance

The detail behind the spend cap, the upload yes and the provenance a generated file carries.

## The cap

- **A run holds its whole estimate while it works.** `altocue generate` books its total under the lock before any job is sent, turns each part into a running job as it goes, and gives back what it did not send when it ends, on failure and on interrupt too.
- **A cancelled job stays on the cap.** `altocue generate --cancel <shot>` asks the provider to stop, and a job already running may still be billed. Once the provider's billing shows no charge, run `altocue generate --not-billed <shot>`. Ask the user first, since it frees money.
- **Every figure is a real number.** A cost or credit count that is not a finite number above zero is refused, and so is a `usd_per_credit` of 0 or less. A free job is imported at `--cost 0`.

## The upload yes

- **A recipe is checked against Higgsfield whatever `--from` says,** because naming the recipe says where it ran.
- **A reservation or import with no recipe is checked when its shot sends project files.** Altocue reads that from `shots.json`, so list the shot first. A shot that is not listed cannot be checked.
- **A file brought in with `--import` is the project's own file,** so a later shot that uses it as `@id` needs the yes too.

## Provenance

- **An upscale, or any file made from an earlier asset, names it.** Import it with `--upstream <shot or job id>`. Its synthetic flags and provenance id carry over, so the claims and disclosure gates still find the person.
- **Altocue's own steps carry it too.** A trim made by `altocue clips cut` and a re-encode made by `altocue master --input` keep the record beside the new file.
- **A file changed outside Altocue does not.** Register it with `--import` and `--upstream`, or the gates cannot tell it is generated.
- **A clip made through a recipe's fal fallback is not that recipe.** Import it with `--from fal` and no `--recipe`, and pass its flags with `--synthetic`.
