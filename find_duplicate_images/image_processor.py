import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import torch
from PIL import Image
from sentence_transformers import SentenceTransformer, util

from find_duplicate_images.utils import get_image_files


class ImageProcessor:
    def __init__(self, embedding_file: str):
        self.embedding_file = embedding_file
        self.default_model = SentenceTransformer("clip-ViT-B-32")

    @staticmethod
    def load_image(filepath):
        return Image.open(filepath)

    def encode_images(self, image_paths, model=None, batch_size=128):
        """
        Encodes a list of images using the given model.
        """
        if model is None:
            model = self.default_model

        all_embeddings = []
        # Use ThreadPoolExecutor for concurrent image loading
        with ThreadPoolExecutor() as executor:
            for start_idx in range(0, len(image_paths), batch_size):
                batch_paths = image_paths[start_idx : start_idx + batch_size]
                images = list(executor.map(self.load_image, batch_paths))
                embeddings = model.encode(
                    images,
                    batch_size=batch_size,
                    convert_to_tensor=True,
                    show_progress_bar=True,
                )
                all_embeddings.append(embeddings)
        return torch.cat(all_embeddings, dim=0)

    def load_embeddings(self, img_folder, batch_size=128):
        """
        Loads the embeddings from the given file or computes them if they don't exist.
        """
        img_names = get_image_files(img_folder)

        img_embedding = None
        existing_img_names = []

        # load embedding
        if (
            os.path.exists(self.embedding_file)
            and os.path.getsize(self.embedding_file) > 0
        ):
            with open(self.embedding_file, "rb") as f:
                existing_data = np.load(f, allow_pickle=True).item()
                img_embedding = existing_data["embeddings"]
                existing_img_names = existing_data["img_names"]
                print("Embedding loaded shape: ", img_embedding.shape)
        else:
            print(f"File {self.embedding_file} not found, loading images...")

        # Only load images that are not in the existing embeddings
        new_img_names = [
            img_name for img_name in img_names if img_name not in existing_img_names
        ]
        print("New images to load:", len(new_img_names))

        if not new_img_names:
            return img_embedding, existing_img_names
        else:
            start_time = time.time()
            new_img_embeddings = self.encode_images(
                new_img_names, batch_size=batch_size
            )
            print(f"Encoding images took {(time.time() - start_time):.2f} seconds")

            # Convert new embeddings to CPU and combine with existing embeddings
            if img_embedding is not None:
                img_embedding = np.concatenate(
                    (img_embedding, new_img_embeddings.cpu().numpy()), axis=0
                )
                img_names = existing_img_names + new_img_names
            else:
                img_embedding = new_img_embeddings.cpu().numpy()
                img_names = new_img_names

            # Save the combined embeddings and image names
            with open(self.embedding_file, "wb") as f:
                np.save(
                    f,
                    {"embeddings": img_embedding, "img_names": img_names},
                    allow_pickle=True,
                )

        return img_embedding, img_names

    @staticmethod
    def search(img_embedding, threshold=0.9, top_k=10, limit=None):
        MAX_THRESHOLD = 1

        duplicates = util.paraphrase_mining_embeddings(img_embedding, top_k=top_k)
        near_duplicates = [
            entry
            for entry in duplicates
            if entry[0] <= MAX_THRESHOLD and entry[0] > threshold
        ]

        if limit is not None:
            near_duplicates = near_duplicates[:limit]

        return near_duplicates

    @staticmethod
    def generate_image_pairs(near_duplicates, all_img_names):
        """
        Generate image pairs from the near duplicates list.

        Ignores images that are not in the image folder.
        """
        # Collect a set of all image names that need to be checked
        required_files = {
            all_img_names[embedding_idx1]
            for _, embedding_idx1, embedding_idx2 in near_duplicates
        }.union(
            {
                all_img_names[embedding_idx2]
                for _, embedding_idx1, embedding_idx2 in near_duplicates
            }
        )

        # Use set comprehensions for quick lookup
        existing_files = {
            img_name for img_name in required_files if os.path.exists(img_name)
        }

        return [
            (all_img_names[embedding_idx1], all_img_names[embedding_idx2], similarity)
            for (similarity, embedding_idx1, embedding_idx2) in near_duplicates
            if all_img_names[embedding_idx1] in existing_files
            and all_img_names[embedding_idx2] in existing_files
        ]
