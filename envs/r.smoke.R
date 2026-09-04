#!/usr/bin/env Rscript
# r.smoke.R — the D3 verification for the `r` env.
#
# Same contract as the nine Python smoke tests (assemble + load + do real work, inside
# the built arm64 image), in the env's own language. `r-base` ships no Python, so this
# is a `.R` run by `Rscript`; builder/build-env.sh picks the runner from the extension.
#
# What is actually at risk here, in rough order:
#   - r-sf / r-terra link the SAME GDAL/PROJ/GEOS natives that broke fieldwork under
#     pip. This is the arm64 assemble risk in this env, so the checks open and write
#     real files rather than stopping at library().
#   - The BLAS/LAPACK path: R's numerics come from libopenblas here, and an R that
#     loads but cannot solve a linear system is the classic "solved, doesn't work".
#   - `Rcpp::sourceCpp()`, which needs a working C++ toolchain in the image. R's
#     compiler is `aarch64-conda-linux-gnu-c++`; there is deliberately no plain `g++`
#     on PATH, so this proves R's own build path rather than a shell assumption.
#   - The r45 build family. Every r-* package in conda-forge is built against a
#     specific R minor version, and this env is pinned to the 4.5 line because no r46
#     builds exist anywhere in the channel (see envs/r.yaml). Like `dft`'s MPI-flavour
#     check, that identity lives in the build string and NOT in the lock (DESIGN OQ2),
#     so it is asserted from conda-meta inside the finished image.
#
# Uses only base R plus the env's own packages.

FAILURES <- character(0)

check <- function(name, expr) {
  # Warnings are surfaced but must NOT abort the check. A `warning=` handler in
  # tryCatch would unwind at the first warning and leave the rest of the body
  # unexecuted while this function still reported `ok` — the same vacuous-pass shape
  # that let `dft` nearly ship a serial gpaw. withCallingHandlers + invokeRestart
  # prints the warning and carries on, so only a real error can pass silently, and it
  # cannot, because that is the tryCatch branch.
  ok <- tryCatch(
    withCallingHandlers({
      force(expr)
      TRUE
    }, warning = function(w) {
      cat(sprintf("  warn %s: %s\n", name, conditionMessage(w)))
      invokeRestart("muffleWarning")
    }),
    error = function(e) {
      FAILURES <<- c(FAILURES, name)
      cat(sprintf("  FAIL %s: %s\n", name, conditionMessage(e)))
      FALSE
    })
  if (isTRUE(ok)) cat(sprintf("  ok   %s\n", name))
  invisible(ok)
}

stopifnot_msg <- function(cond, msg) if (!isTRUE(cond)) stop(msg, call. = FALSE)

tmpd <- tempfile("rsmoke"); dir.create(tmpd)
on.exit(unlink(tmpd, recursive = TRUE), add = TRUE)

# --- 1. library() loads ---------------------------------------------------------
HEADLINE <- c("MASS", "Matrix", "survival", "mgcv",
              "dplyr", "tidyr", "readr", "purrr", "tibble", "stringr", "ggplot2",
              "data.table", "arrow", "sf", "terra",
              "glmnet", "randomForest", "caret",
              "knitr", "rmarkdown", "jsonlite", "Rcpp", "ragg")
cat("[smoke] 1. library() loads\n")
for (pkg in HEADLINE) {
  check(paste0("library(", pkg, ")"),
        suppressPackageStartupMessages(library(pkg, character.only = TRUE)))
}

# --- 2. the interpreter and its build identity ----------------------------------
cat("[smoke] 2. interpreter + build identity\n")

check("R is the 4.5 line on aarch64", {
  v <- getRversion()
  stopifnot_msg(v >= "4.5" && v < "4.6",
                sprintf("R %s is outside the 4.5 line this env pins (see envs/r.yaml)", v))
  arch <- R.version$arch
  stopifnot_msg(grepl("aarch64", arch), sprintf("R reports arch=%s, expected aarch64", arch))
  cat(sprintf("       R %s on %s\n", v, arch))
})

check("every r-* package is an r45 build (OQ2: not in the lock)", {
  # The lock records `name version` only, so a build-family flip is invisible to it and
  # to the reconciler. Read the truth out of conda-meta, the same defence dft uses for
  # its MPI flavour. An r44 (or r46) package sneaking in is an ABI mismatch waiting to
  # segfault, not a cosmetic difference.
  meta <- Sys.glob(file.path(R.home(), "..", "..", "conda-meta", "r-*.json"))
  if (!length(meta)) meta <- Sys.glob("/opt/conda/conda-meta/r-*.json")
  stopifnot_msg(length(meta) > 50,
                sprintf("found only %d r-* conda-meta records; expected the full CRAN layer",
                        length(meta)))
  bad <- character(0)
  for (f in meta) {
    rec <- jsonlite::fromJSON(f)
    b <- rec$build
    if (grepl("^r[0-9][0-9]", b) && !grepl("^r45", b)) bad <- c(bad, paste0(rec$name, " ", b))
  }
  stopifnot_msg(length(bad) == 0,
                paste0("non-r45 builds present: ", paste(head(bad, 6), collapse = ", ")))
  cat(sprintf("       %d r-* conda-meta records, all r45\n", length(meta)))
})

check("numerics come from openblas, and actually compute", {
  # R linked against a broken or missing BLAS still starts fine; it fails here.
  set.seed(1)
  n <- 60
  A <- matrix(rnorm(n * n), n, n) + diag(n) * n   # well-conditioned
  b <- rnorm(n)
  x <- solve(A, b)
  resid <- max(abs(A %*% x - b))
  stopifnot_msg(resid < 1e-8, sprintf("solve() residual %.3g is too large", resid))
  # Symmetric eigen: exercises LAPACK, and the eigenvalues of a known matrix are known.
  S <- crossprod(A)
  ev <- eigen(S, symmetric = TRUE, only.values = TRUE)$values
  stopifnot_msg(all(ev > 0), "crossprod(A) should be positive definite")
  bl <- tryCatch(La_library(), error = function(e) "")
  cat(sprintf("       solve() residual %.2g; LAPACK from %s\n", resid,
              if (nzchar(bl)) basename(bl) else "(unreported)"))
})

# --- 3. tidyverse + tabular -----------------------------------------------------
cat("[smoke] 3. tidyverse + tabular\n")

check("dplyr group_by/summarise gives the arithmetic we expect", {
  d <- data.frame(g = c("a", "a", "b", "b", "b"), v = c(1, 3, 10, 20, 30))
  out <- dplyr::summarise(dplyr::group_by(d, g), m = mean(v), n = dplyr::n(),
                          .groups = "drop")
  out <- out[order(out$g), ]
  stopifnot_msg(nrow(out) == 2, "expected two groups")
  stopifnot_msg(abs(out$m[1] - 2) < 1e-12 && abs(out$m[2] - 20) < 1e-12,
                sprintf("group means wrong: %s", paste(out$m, collapse = ", ")))
  stopifnot_msg(all(out$n == c(2, 3)), "group counts wrong")
})

check("readr writes and re-reads a CSV byte-faithfully", {
  p <- file.path(tmpd, "t.csv")
  d <- tibble::tibble(i = 1:3, x = c(1.5, 2.5, 3.5), s = c("a", "b", "c"))
  readr::write_csv(d, p)
  back <- readr::read_csv(p, show_col_types = FALSE)
  stopifnot_msg(nrow(back) == 3 && ncol(back) == 3, "CSV round-trip changed shape")
  stopifnot_msg(all(abs(back$x - d$x) < 1e-12), "CSV round-trip changed numbers")
})

check("data.table fread/fwrite + a keyed join", {
  p <- file.path(tmpd, "t.dt.csv")
  DT <- data.table::data.table(id = 1:5, v = (1:5) * 2)
  data.table::fwrite(DT, p)
  back <- data.table::fread(p)
  stopifnot_msg(identical(nrow(back), 5L), "fread lost rows")
  lut <- data.table::data.table(id = c(2L, 4L), label = c("two", "four"))
  data.table::setkey(back, id); data.table::setkey(lut, id)
  j <- lut[back]
  stopifnot_msg(sum(!is.na(j$label)) == 2, "keyed join matched the wrong number of rows")
})

check("arrow writes and reads Parquet (native libarrow)", {
  # arrow is a big C++ library behind a thin R binding — the same shape of risk as PDAL
  # in `pointcloud`. Writing a real Parquet file and reading it back drives it properly.
  p <- file.path(tmpd, "t.parquet")
  d <- data.frame(i = 1:1000, x = as.numeric(1:1000) / 7)
  arrow::write_parquet(d, p)
  stopifnot_msg(file.exists(p) && file.size(p) > 0, "no parquet file produced")
  back <- as.data.frame(arrow::read_parquet(p))
  stopifnot_msg(nrow(back) == 1000, sprintf("parquet round-trip gave %d rows", nrow(back)))
  stopifnot_msg(max(abs(back$x - d$x)) < 1e-12, "parquet round-trip changed values")
})

# --- 4. the geospatial natives (GDAL / PROJ / GEOS) -----------------------------
cat("[smoke] 4. geospatial natives (the fieldwork failure mode)\n")

check("sf reports its GDAL/PROJ/GEOS versions", {
  ext <- sf::sf_extSoftVersion()
  for (k in c("GDAL", "PROJ", "GEOS")) {
    stopifnot_msg(nzchar(ext[[k]]), sprintf("sf reports no %s version", k))
  }
  cat(sprintf("       GDAL %s / PROJ %s / GEOS %s\n", ext[["GDAL"]], ext[["PROJ"]], ext[["GEOS"]]))
})

check("sf reprojects WGS84 -> WebMercator (PROJ)", {
  # Same point and same expected window as the Python smoke tests use, so the two
  # language stacks are checked against one answer rather than two.
  pt <- sf::st_sfc(sf::st_point(c(-83.0, 40.0)), crs = 4326)
  m <- sf::st_coordinates(sf::st_transform(pt, 3857))
  stopifnot_msg(m[1, "X"] > -9.3e6 && m[1, "X"] < -9.2e6,
                sprintf("x=%f outside expected window", m[1, "X"]))
  stopifnot_msg(m[1, "Y"] > 4.8e6 && m[1, "Y"] < 4.9e6,
                sprintf("y=%f outside expected window", m[1, "Y"]))
})

check("sf does GEOS geometry ops", {
  poly <- sf::st_sfc(sf::st_polygon(list(rbind(c(0, 0), c(2, 0), c(2, 2), c(0, 2), c(0, 0)))))
  stopifnot_msg(abs(as.numeric(sf::st_area(poly)) - 4) < 1e-9, "st_area wrong")
  buf <- sf::st_buffer(sf::st_sfc(sf::st_point(c(0, 0))), 1)
  a <- as.numeric(sf::st_area(buf))
  stopifnot_msg(a > 3.0 && a < 3.15, sprintf("buffered unit circle area %.4f", a))
})

check("sf writes + reads a GeoPackage (GDAL vector I/O)", {
  p <- file.path(tmpd, "pts.gpkg")
  d <- sf::st_sf(id = 1:3,
                 geometry = sf::st_sfc(sf::st_point(c(0, 0)), sf::st_point(c(1, 1)),
                                       sf::st_point(c(2, 2)), crs = 4326))
  sf::st_write(d, p, quiet = TRUE)
  back <- sf::st_read(p, quiet = TRUE)
  stopifnot_msg(nrow(back) == 3, sprintf("GeoPackage round-trip gave %d features", nrow(back)))
  stopifnot_msg(!is.na(sf::st_crs(back)$epsg) && sf::st_crs(back)$epsg == 4326,
                "GeoPackage round-trip lost the CRS")
})

check("terra writes + reads a GeoTIFF (GDAL raster I/O)", {
  p <- file.path(tmpd, "r.tif")
  r <- terra::rast(nrows = 10, ncols = 10, xmin = 0, xmax = 10, ymin = 0, ymax = 10,
                   crs = "EPSG:4326")
  terra::values(r) <- seq_len(100)
  terra::writeRaster(r, p, overwrite = TRUE)
  stopifnot_msg(file.exists(p) && file.size(p) > 0, "no GeoTIFF produced")
  back <- terra::rast(p)
  stopifnot_msg(terra::ncell(back) == 100, "GeoTIFF round-trip changed cell count")
  v <- terra::values(back)
  stopifnot_msg(max(abs(as.numeric(v) - seq_len(100))) < 1e-6,
                "GeoTIFF round-trip changed pixel values")
  stopifnot_msg(grepl("4326", terra::crs(back)), "GeoTIFF round-trip lost the CRS")
})

# --- 5. modelling ----------------------------------------------------------------
cat("[smoke] 5. modelling\n")

check("glmnet recovers a known sparse signal", {
  set.seed(42)
  n <- 200; p <- 20
  X <- matrix(rnorm(n * p), n, p)
  beta <- c(3, -2, rep(0, p - 2))
  y <- as.numeric(X %*% beta + rnorm(n) * 0.1)
  fit <- glmnet::cv.glmnet(X, y, nfolds = 5)
  co <- as.numeric(coef(fit, s = "lambda.min"))[-1]
  stopifnot_msg(co[1] > 2 && co[2] < -1,
                sprintf("glmnet did not recover the signal: %.2f, %.2f", co[1], co[2]))
  stopifnot_msg(sum(abs(co[3:p]) > 0.5) == 0, "glmnet kept noise coefficients")
})

check("randomForest fits and predicts better than chance", {
  set.seed(7)
  n <- 300
  d <- data.frame(x1 = rnorm(n), x2 = rnorm(n))
  d$y <- factor(ifelse(d$x1 + d$x2 > 0, "pos", "neg"))
  fit <- randomForest::randomForest(y ~ x1 + x2, data = d, ntree = 100)
  acc <- mean(predict(fit, d) == d$y)
  stopifnot_msg(acc > 0.85, sprintf("randomForest in-sample accuracy only %.3f", acc))
})

check("caret trains through its formula/resampling machinery", {
  set.seed(11)
  d <- data.frame(x = rnorm(100)); d$y <- 2 * d$x + rnorm(100) * 0.1
  fit <- caret::train(y ~ x, data = d, method = "lm",
                      trControl = caret::trainControl(method = "cv", number = 3))
  slope <- coef(fit$finalModel)[["x"]]
  stopifnot_msg(abs(slope - 2) < 0.2, sprintf("caret/lm slope %.3f, expected ~2", slope))
})

# --- 6. reporting + graphics -----------------------------------------------------
cat("[smoke] 6. reporting + graphics\n")

check("ggplot2 computes a layer's statistics", {
  # ggplot_build is where ggplot2 actually does the work; constructing the object alone
  # proves almost nothing, because ggplot2 is lazy.
  p <- ggplot2::ggplot(data.frame(x = c(1, 1, 2, 2, 2)), ggplot2::aes(x = factor(x))) +
    ggplot2::geom_bar()
  built <- ggplot2::ggplot_build(p)
  counts <- sort(built$data[[1]]$count)
  stopifnot_msg(identical(as.numeric(counts), c(2, 3)),
                sprintf("geom_bar counts %s, expected 2 and 3", paste(counts, collapse = ",")))
})

check("ragg writes a real PNG and it contains pixels", {
  # Same standard as `viz`: a figure file that exists is not proof; it has to be a
  # decodable PNG of the requested size, not a zero-byte or blank artifact.
  p <- file.path(tmpd, "plot.png")
  ragg::agg_png(p, width = 400, height = 300, res = 100)
  print(ggplot2::ggplot(data.frame(x = 1:10, y = (1:10)^2), ggplot2::aes(x, y)) +
          ggplot2::geom_line() + ggplot2::geom_point())
  dev.off()
  stopifnot_msg(file.exists(p), "no PNG written")
  sz <- file.size(p)
  stopifnot_msg(sz > 1000, sprintf("PNG is only %d bytes — likely blank", sz))
  # Verify the PNG signature and read width/height out of the IHDR chunk.
  con <- file(p, "rb"); raw8 <- readBin(con, "raw", 33); close(con)
  sig <- as.integer(raw8[1:8])
  stopifnot_msg(identical(sig, c(137L, 80L, 78L, 71L, 13L, 10L, 26L, 10L)),
                "file does not start with the PNG signature")
  be32 <- function(b) sum(as.integer(b) * c(2^24, 2^16, 2^8, 1))
  w <- be32(raw8[17:20]); h <- be32(raw8[21:24])
  stopifnot_msg(w == 400 && h == 300, sprintf("PNG is %dx%d, expected 400x300", w, h))
  cat(sprintf("       %dx%d PNG, %d bytes\n", w, h, sz))
})

check("knitr knits an .Rmd, evaluating its code", {
  rmd <- file.path(tmpd, "t.Rmd")
  writeLines(c("---", "title: smoke", "---", "",
               "```{r}", "sum(1:10)", "```"), rmd)
  out <- knitr::knit(rmd, output = file.path(tmpd, "t.md"), quiet = TRUE)
  txt <- paste(readLines(out), collapse = "\n")
  stopifnot_msg(grepl("55", txt), "knitr did not evaluate the chunk (no 55 in output)")
})

check("rmarkdown renders to HTML (needs pandoc, which the spec names)", {
  # This is the check that justifies `pandoc` being in envs/r.yaml. Without it
  # rmarkdown installs cleanly and then cannot produce anything but markdown.
  stopifnot_msg(rmarkdown::pandoc_available(),
                "pandoc is not available — rmarkdown::render() cannot produce HTML")
  rmd <- file.path(tmpd, "r.Rmd")
  writeLines(c("---", "title: smoke", "output: html_document", "---", "",
               "```{r}", "sum(1:10)", "```"), rmd)
  html <- rmarkdown::render(rmd, output_file = "r.html", output_dir = tmpd, quiet = TRUE)
  stopifnot_msg(file.exists(html) && file.size(html) > 1000, "no HTML produced")
  txt <- paste(readLines(html, warn = FALSE), collapse = "\n")
  stopifnot_msg(grepl("55", txt), "rendered HTML does not contain the computed value")
  cat(sprintf("       pandoc %s\n", as.character(rmarkdown::pandoc_version())))
})

# --- 7. compiled extensions (Rcpp) ----------------------------------------------
cat("[smoke] 7. compiled extensions\n")

check("Rcpp::sourceCpp compiles and runs C++ in the image", {
  # A large share of CRAN's speed comes through Rcpp, so an R image that cannot compile
  # is a trap. Note there is deliberately no plain `g++` on PATH: R's compiler here is
  # aarch64-conda-linux-gnu-c++, which is what `R CMD config CXX` reports.
  cxx <- system2("R", c("CMD", "config", "CXX"), stdout = TRUE, stderr = TRUE)
  cpp <- file.path(tmpd, "addup.cpp")
  writeLines(c("#include <Rcpp.h>",
               "using namespace Rcpp;",
               "// [[Rcpp::export]]",
               "double addup(NumericVector x) {",
               "  double s = 0.0;",
               "  for (int i = 0; i < x.size(); i++) s += x[i];",
               "  return s;",
               "}"), cpp)
  # `env=` is explicit on purpose: sourceCpp defaults to parent.frame(), and this body
  # runs as a lazily-evaluated argument, so which frame that is is not obvious. Naming
  # a fresh environment makes the lookup below deterministic.
  target <- new.env()
  Rcpp::sourceCpp(cpp, env = target)
  got <- get("addup", envir = target)(c(1, 2, 3.5))
  stopifnot_msg(abs(got - 6.5) < 1e-12, sprintf("compiled addup() returned %f", got))
  cat(sprintf("       compiled via %s\n", paste(cxx, collapse = " ")))
})

# --- verdict --------------------------------------------------------------------
cat(paste0("[smoke] ", strrep("-", 50), "\n"))
if (length(FAILURES)) {
  cat(sprintf("[smoke] FAILED: %d check(s): %s\n", length(FAILURES),
              paste(FAILURES, collapse = ", ")))
  quit(status = 1)
}
cat(sprintf("[smoke] PASSED: r env assembles, loads, and works on %s (R %s) — verified.\n",
            R.version$platform, getRversion()))
