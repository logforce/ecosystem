# License Scope

Effective project licensing decision: 12 September 2026.

This is a mixed-license working repository. The root Apache license is not a
blanket grant over every file, asset, model or historical commit. The exact
public-release scope is recorded in [PUBLICATION.json](PUBLICATION.json).

## Grants for Included Project Material

Project-owned software, configuration, schemas, build recipes, examples and tests
identified as `Apache-2.0` in the manifest are licensed under the Apache License,
Version 2.0, reproduced in [LICENSE](LICENSE).

Public specification and prose identified as `CC-BY-4.0` in the manifest are
licensed under Creative Commons Attribution 4.0 International, reproduced in
[LICENSES/CC-BY-4.0.txt](LICENSES/CC-BY-4.0.txt). Attribute the work to "ecOS and
CogPOSIX contributors", retain supplied individual attribution notices, provide
the license link and indicate modifications as the license requires.

Project-owned source-code examples and machine-readable schemas embedded in those
public documents are additionally offered under Apache-2.0, so they may be used
under that software license. Quoted third-party examples retain their own terms.
The unmodified license texts retain their licensors' notices and terms; their
presence is not a relicensing of the licenses themselves.

These grants permit commercial use subject to their respective terms. They do
not require a subscription, a project account, telemetry, LOGFORCE installation,
publication of Apache-licensed modifications, or payment for ordinary conformance
testing. The community runtime and intended community OS remain independently usable.

## Exclusions

### Optional ETE-X component

Effective 22 October 2026, the original ETE-X implementation, worker, semantic
mapper, synthetic corpus and marked ETE-X tests are separately licensed under
**CC BY-NC-ND 4.0**, as authorized by their rights holder. Their exact paths are
identified in the publication manifest. See the
[ETE-X license](https://github.com/logforce/ecosystem/blob/main/components/ete-x/LICENSE.md) and
[third-party notices](https://github.com/logforce/ecosystem/blob/main/components/ete-x/NOTICE.md).

This is not an Apache-2.0 grant for ETE-X. Commercial use and distribution of
adapted ETE-X material are not granted by that CC license. The default community
runtime does not compile or link ETE-X; `--features etex` explicitly selects this
separately licensed component. The process-backend source package therefore
contains separately licensed files, reflected in its Cargo metadata. The existing
Apache-2.0 core and its thin dispatch/configuration code retain their license.
Third-party demonstration model assets retain their own original terms.

Website markup, CSS and JavaScript are Apache-2.0; editorial prose within
`index.html` is additionally identified here as CC BY 4.0, not solely by the HTML
file's software classification. Images are separate assets, not embedded prose.
The two explicitly listed logos use `LicenseRef-Brand-Reserved`: publication does
not grant an open artwork license. See the [artwork notice](assets/BRAND-NOTICE.md).
The generated computing illustration is CC BY 4.0 to the extent copyright applies.
The reader's generated document bundle preserves the licenses of its constituent
documents and license texts; the bundle's prose classification does not relicense
embedded third-party notices. Marked retains MIT; DOMPurify is distributed under
its Apache-2.0 option. See the [website notices](site/THIRD-PARTY.md).

No public license is granted here for files omitted from the publication manifest,
including internal strategy or contract drafts, original planning documents,
unreleased art, proprietary extensions, private data or material in other
repositories. Existing third-party or separately granted rights are unaffected.

LOGFORCE intelligence, models, enterprise sensors and commercial modules are
separately distributed under their own agreements. The open event schema and basic
integration bridge, when implemented and explicitly added to the manifest, will
be Apache-2.0. No proprietary engine is licensed through an example mentioning it.

Linux, dependencies, firmware, model weights and datasets retain their original
licenses. Future OS distributions require component inventories and applicable
source/notice delivery. A commercial OS offering is not a proprietary relicensing
of its open-source components. The optional ETE-X directory includes the two
explicitly inventoried third-party ONNX demonstration models; other model weights
are not included in this release.

## Patents, Brands and Future Contributions

Apache-2.0 includes its stated contributor patent grant; CC BY does not license
patent rights. Neither document promises that every independent implementation of
the proposed specification is free of third-party patent claims. Specification IP
governance must be established before formal standardization claims.

Brand permissions and certification claims are described in [TRADEMARKS.md](TRADEMARKS.md).
They do not add restrictions to the Apache or CC BY grants. Contribution licensing
is described in [CONTRIBUTING.md](CONTRIBUTING.md).

Future license changes cannot retract rights already granted to compliant
recipients under earlier releases. Adding files to the publication manifest is an
explicit disclosure and licensing decision; unknown files remain excluded.
