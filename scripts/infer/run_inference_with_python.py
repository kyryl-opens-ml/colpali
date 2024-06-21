import typer
import torch
from transformers import AutoProcessor

from PIL import Image
import requests

from custom_colbert.models.paligemma_colbert_architecture import ColPali
from custom_colbert.trainer.retrieval_evaluator import CustomEvaluator


def process_images(processor, images, max_length: int = 50):
    texts_doc = ["Describe the image."] * len(images)
    images = [image.convert("RGB") for image in images]

    batch_doc = processor(
        text=texts_doc,
        images=images,
        return_tensors="pt",
        padding="longest",
        max_length=max_length + processor.image_seq_length,
    )
    # batch_doc = {f"doc_{k}": v for k, v in batch_doc.items()}
    return batch_doc


def process_queries(processor, queries, mock_image, max_length: int = 50):
    texts_query = []
    for query in queries:
        query = f"Question: {query}<unused0><unused0><unused0><unused0><unused0>"
        texts_query.append(query)

    batch_query = processor(
        images=[mock_image.convert("RGB")] * len(texts_query),
        # NOTE: the image is not used in batch_query but it is required for calling the processor
        text=texts_query,
        return_tensors="pt",
        padding="longest",
        max_length=max_length + processor.image_seq_length)
    del batch_query["pixel_values"]

    batch_query["input_ids"] = batch_query["input_ids"][..., processor.image_seq_length:]
    batch_query["attention_mask"] = batch_query["attention_mask"][..., processor.image_seq_length:]

    # batch_query = {f"query_{k}": v for k, v in batch_query.items()}
    return batch_query


def pdf_to_images(pdf_path: str):
    from pdf2image import convert_from_path
    images = convert_from_path(pdf_path)
    return images


def main() -> None:
    """Example script to run inference with ColPali"""

    # Load model
    model_name = "coldoc/colpali-3b-mix-448"
    model = ColPali.from_pretrained("google/paligemma-3b-mix-448", torch_dtype=torch.bfloat16, device_map="cuda").eval()
    model.load_adapter(model_name)
    processor = AutoProcessor.from_pretrained(model_name)
    device = model.device

    # images from pdf pages
    images = pdf_to_images("example.pdf")
    queries = ["What is the carbon footprint of the work ?", "What is the scoring prompt used?"]

    # run inference - queries
    from torch.utils.data import DataLoader
    dataloader = DataLoader(
        queries,
        batch_size=4,
        shuffle=False,
        collate_fn=lambda x: process_queries(processor, x, images[0]),
    )

    qs = []
    for batch_query in dataloader:
        with torch.no_grad():
            batch_query = {k: v.to(device) for k, v in batch_query.items()}
            embeddings_query = model(**batch_query)
        qs.extend(list(torch.unbind(embeddings_query.to("cpu"))))

    # run inference - docs
    dataloader = DataLoader(
        images,
        batch_size=4,
        shuffle=False,
        collate_fn=lambda x: process_images(processor, x),
    )
    ds = []
    for batch_doc in dataloader:
        with torch.no_grad():
            batch_doc = {k: v.to(device) for k, v in batch_doc.items()}
            embeddings_doc = model(**batch_doc)
        ds.extend(list(torch.unbind(embeddings_doc.to("cpu"))))

    # run evaluation
    retriever_evaluator = CustomEvaluator(is_multi_vector=True)
    scores = retriever_evaluator.evaluate(qs, ds)

    print(scores.shape)
    print(scores)


if __name__ == "__main__":
    typer.run(main)