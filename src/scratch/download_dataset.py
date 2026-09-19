import os
from datasets import load_dataset
import pandas as pd

def main():
    datasets_dir = "datasets"
    os.makedirs(datasets_dir, exist_ok=True)

    print("Fetching 'airesearch/WangchanX-Legal-ThaiCCL-RAG' from Hugging Face...")
    try:
        dataset = load_dataset("airesearch/WangchanX-Legal-ThaiCCL-RAG")
        print(f"Dataset loaded. Available splits: {list(dataset.keys())}")
        
        # Save test split if available, otherwise take train
        split = "test" if "test" in dataset else list(dataset.keys())[0]
        df = pd.DataFrame(dataset[split])
        
        output_path = os.path.join(datasets_dir, f"test-legal.parquet")
        df.to_parquet(output_path, index=False)
        print(f"Successfully saved {len(df)} rows to {output_path}")
        
    except Exception as e:
        print(f"Failed to download/save dataset: {e}")

if __name__ == "__main__":
    main()
