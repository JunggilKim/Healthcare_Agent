# Dataset A annotation workspace

This directory intentionally contains no fabricated human labels. The release subset requires at
least 200 criterion-patient pairs adjudicated against the exact public criterion and structured
patient state, with two independent project reviewers for at least 50 pairs.

Reviewers must label this work **protocol-text adjudication by project reviewers** unless qualified
clinicians actually perform the review. Every row must retain reviewer pseudonyms, independent
timestamps, source and protocol hashes, disagreement state, and the final adjudicated label.

The generated one-trial fixture benchmark is a deterministic engineering smoke dataset and is not
the mandatory Dataset A, a clinical validation dataset, or an independent accuracy benchmark.
