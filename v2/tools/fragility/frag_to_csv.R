#!/usr/bin/env Rscript
# Dump the fragility matrices frag_compute.R stored as .rds into CSV, so the
# Python parity harness can read them without R.
#
#   Rscript frag_to_csv.R DATA_DIR
#
# Reads DATA_DIR/frag_full/{label}.rds, writes {label}_frag.csv (contacts x
# windows, contact names in column 1), {label}_times.csv and {label}_r2.csv.
# Base R only -- no EZFragility, nothing recomputed.

args <- commandArgs(trailingOnly = TRUE)
if (!length(args)) stop("usage: Rscript frag_to_csv.R DATA_DIR")
DIR <- file.path(args[1], "frag_full")
if (!dir.exists(DIR)) stop("no frag_full/ under ", args[1])

for (f in Sys.glob(file.path(DIR, "*.rds"))) {
  sz <- sub("\\.rds$", "", basename(f))
  x <- readRDS(f)

  fm <- x$frag
  rownames(fm) <- x$electrodes
  write.csv(fm, file.path(DIR, paste0(sz, "_frag.csv")), row.names = TRUE)
  write.csv(data.frame(startTime = x$startTimes), file.path(DIR, paste0(sz, "_times.csv")),
            row.names = FALSE)
  write.csv(as.matrix(x$R2), file.path(DIR, paste0(sz, "_r2.csv")), row.names = TRUE)
  lam <- as.matrix(x$lambdas)
  write.csv(lam, file.path(DIR, paste0(sz, "_lambdas.csv")), row.names = TRUE)

  cat(sprintf("%s: frag %s, R2 %s, lambdas %s, %d startTimes\n", sz,
              paste(dim(fm), collapse = "x"),
              paste(dim(as.matrix(x$R2)), collapse = "x"),
              paste(dim(lam), collapse = "x"), length(x$startTimes)))
}
