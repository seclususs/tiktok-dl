import sys

from ttdl.cli import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nINTERRUPT execution aborted by user")
        sys.exit(130)
