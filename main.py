import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Re-export for backward compatibility — scheduler and other callers use:
#   from main import run_agent
#   from main import run_pipeline
from pipeline.legacy import run_agent, run_pipeline  # noqa: F401

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--legacy", action="store_true",
                        help="Force legacy run_agent() path instead of TradingPipeline")
    args = parser.parse_args()
    if args.legacy:
        run_agent(dry_run=args.dry_run)
    else:
        run_pipeline(dry_run=args.dry_run)
