import sys
import pandas as pd

# Usage: python profile_dataset.py <file.csv> [label_column]
path = sys.argv[1]
label_col = sys.argv[2] if len(sys.argv) > 2 else None

df = pd.read_csv(path, low_memory=False)

print("File:", path)
print("Rows:", len(df), " Columns:", df.shape[1])
print("\nColumn names and types:")
print(df.dtypes.to_string())

print("\nMissing values (only columns with any):")
missing = df.isna().sum()
print(missing[missing > 0].to_string() if missing.any() else "none")

print("\nDuplicate rows:", df.duplicated().sum())

if label_col is None:
    guesses = [c for c in df.columns if c.lower() in ("label", "type", "attack", "attack_cat", "class")]
    label_col = guesses[0] if guesses else None
    print("\nGuessed label column:", label_col)

if label_col:
    print("\nClass counts:")
    counts = df[label_col].value_counts()
    print(counts.to_string())
    print("\nClass share (%):")
    print((counts / len(df) * 100).round(2).to_string())
