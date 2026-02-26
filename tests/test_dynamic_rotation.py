import os
import time
import datetime
import logging
import shutil
from pathlib import Path
from mirror.logger.handler import DynamicGzipRotatingFileHandler

def test_minute_rotation():
    test_base = Path("test_logs_rotation")
    if test_base.exists():
        shutil.rmtree(test_base)
    test_base.mkdir()

    folder_template = "{year}/{month}"
    # Use minute-level resolution for testing
    filename_template = "test-{hour}-{minute}.log"
    
    handler = DynamicGzipRotatingFileHandler(
        base_path=test_base,
        folder_template=folder_template,
        filename_template=filename_template,
        gzip_enabled=False # Disable gzip for easier file check
    )
    
    logger = logging.getLogger("test_rotation")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    
    try:
        # 1. First log at current time
        now = datetime.datetime.now()
        logger.info("First log message")
        first_path = Path(handler.baseFilename)
        assert first_path.exists(), "First log file should be created"
        
        # 2. Simulate time passage by 1 minute
        # We need to create a record with a different timestamp because emit() uses record.created
        future_time = now + datetime.timedelta(minutes=1)
        record = logger.makeRecord(
            name=logger.name,
            level=logging.INFO,
            fn="test_dynamic_rotation.py",
            lno=0,
            msg="Second log message (rotated)",
            args=(),
            exc_info=None
        )
        record.created = future_time.timestamp()
        
        # Manually call emit to simulate rotation
        handler.emit(record)
        
        second_path = Path(handler.baseFilename)
        
        print(f"First path: {first_path}")
        print(f"Second path: {second_path}")
        
        assert first_path != second_path, "Path should have changed after 1 minute"
        assert second_path.exists(), "Second log file should be created"
        
        # Verify both files contain their respective messages
        with open(first_path, "r") as f:
            content = f.read()
            assert "First log message" in content
            
        with open(second_path, "r") as f:
            content = f.read()
            assert "Second log message (rotated)" in content
            
        print("Minute-level rotation test passed!")

    finally:
        handler.close()
        if test_base.exists():
            shutil.rmtree(test_base)

def test_gzip_rotation():
    test_base = Path("test_logs_gzip")
    if test_base.exists():
        shutil.rmtree(test_base)
    test_base.mkdir()

    handler = DynamicGzipRotatingFileHandler(
        base_path=test_base,
        folder_template="logs",
        filename_template="test-{minute}.log",
        gzip_enabled=True
    )
    
    logger = logging.getLogger("test_gzip")
    logger.addHandler(handler)
    
    try:
        now = datetime.datetime.now()
        logger.info("Message 1")
        old_path = Path(handler.baseFilename)
        
        # Rotate by 1 minute
        future_time = now + datetime.timedelta(minutes=1)
        record = logger.makeRecord(logger.name, logging.INFO, "test.py", 0, "Message 2", (), None)
        record.created = future_time.timestamp()
        handler.emit(record)
        
        # Check if old file was gzipped
        gzip_path = old_path.with_suffix(old_path.suffix + ".gz")
        print(f"Checking for gzip file: {gzip_path}")
        assert gzip_path.exists(), "Old log file should be gzipped"
        assert not old_path.exists(), "Old uncompressed log file should be removed"
        
        print("Gzip rotation test passed!")

    finally:
        handler.close()
        if test_base.exists():
            shutil.rmtree(test_base)

if __name__ == "__main__":
    test_minute_rotation()
    test_gzip_rotation()
